"""Compute is replaceable, the disk is the workspace, and deletion means deletion.

Provider behaviour runs against a mocked Fly Machines API; lifecycle decisions run
against fakes. The SQL fences are covered by the PostgreSQL contract tests.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from src.config import settings
from src.platform.sandbox import WorkerLost, WorkspaceDiskLost, WorkspaceUnavailable
from src.platform.sandbox.providers.fly.provider import FlyRuntimeProvider
from src.platform.workspace.lifecycle import WorkbenchLifecycle

IMAGE = "registry.fly.io/app@sha256:" + "a" * 64


def resource(**overrides):
    value = dict(
        provider="fly",
        app="app",
        resource_id="machine-lost",
        project_id="project",
        workbench_id=str(uuid4()),
        workbench_generation=str(uuid4()),
        volume_id="disk-one",
        image_id=IMAGE,
        allocation_pending=False,
    )
    value.update(overrides)
    return value


def machine(binding, machine_id, state="stopped", volume="disk-one"):
    return dict(
        id=machine_id,
        state=state,
        config=dict(
            image=IMAGE,
            mounts=[dict(volume=volume, path="/data")],
            metadata={
                k: binding[k] for k in ("workbench_id", "workbench_generation", "project_id")
            },
        ),
    )


async def fly(monkeypatch, handle):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "backend-only")
    client = httpx.AsyncClient(
        base_url="https://api.machines.dev", transport=httpx.MockTransport(handle)
    )
    return client, FlyRuntimeProvider(client=client)


def host_for(binding):
    return SimpleNamespace(resource=binding, artifact=IMAGE, restore_point=None, attached=False)


async def test_a_destroyed_machine_is_lost_not_startable(monkeypatch):
    binding = resource()

    def handle(request):
        return httpx.Response(200, json=machine(binding, "machine-lost", state="destroyed"))

    client, provider = await fly(monkeypatch, handle)
    async with client:
        with pytest.raises(WorkerLost):
            await provider.resume(host_for(binding))


async def test_recreate_starts_new_compute_on_the_same_disk(monkeypatch):
    binding, calls = resource(allocation_pending=True), []

    def handle(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "GET" and request.url.path.endswith("/machines"):
            return httpx.Response(200, json=[])  # The lost Machine is gone.
        if request.url.path.endswith("/volumes/disk-one"):
            return httpx.Response(200, json={"id": "disk-one", "region": "sin", "state": "created"})
        if request.method == "POST" and request.url.path.endswith("/machines"):
            return httpx.Response(200, json={"id": "machine-new"})
        return httpx.Response(200, json={"state": "started"})

    client, provider = await fly(monkeypatch, handle)
    host = host_for(binding)
    async with client:
        await provider.recreate(host)
    created = next(body for method, path, body in calls if method == "POST")
    assert created["config"]["mounts"] == [{"volume": "disk-one", "path": "/data"}]
    assert created["config"]["metadata"]["workbench_generation"] == binding["workbench_generation"]
    assert host.resource["resource_id"] == "machine-new"
    assert host.resource["volume_id"] == "disk-one"
    assert host.resource["allocation_pending"] is False
    assert not any(method == "DELETE" for method, _, _ in calls)


async def test_recreate_adopts_compute_created_before_a_lost_reply(monkeypatch):
    binding, calls = resource(allocation_pending=True), []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/machines"):
            return httpx.Response(200, json=[machine(binding, "machine-new")])
        if request.method == "GET" and request.url.path.endswith("/machines/machine-new"):
            return httpx.Response(200, json=machine(binding, "machine-new"))
        return httpx.Response(200, json={"state": "started"})

    client, provider = await fly(monkeypatch, handle)
    host = host_for(binding)
    async with client:
        await provider.recreate(host)
    assert host.resource["resource_id"] == "machine-new"
    assert ("POST", "/v1/apps/app/machines") not in calls  # Never a second Machine.


async def test_recreate_reports_a_lost_disk_without_creating(monkeypatch):
    binding, calls = resource(allocation_pending=True), []

    def handle(request):
        calls.append(request.method)
        if request.url.path.endswith("/volumes/disk-one"):
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=[])

    client, provider = await fly(monkeypatch, handle)
    async with client:
        with pytest.raises(WorkspaceDiskLost):
            await provider.recreate(host_for(binding))
    assert calls == ["GET", "GET"]


async def test_recreate_requires_a_persisted_intent(monkeypatch):
    client, provider = await fly(monkeypatch, lambda request: pytest.fail("no request"))
    async with client:
        with pytest.raises(ValueError, match="intent"):
            await provider.recreate(host_for(resource()))


async def test_destroy_removes_every_machine_and_the_disk_of_one_generation(monkeypatch):
    binding, deleted = resource(), []
    orphan = FlyRuntimeProvider._volume_name(binding["workbench_generation"])

    def handle(request):
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(404 if "machine-lost" in request.url.path else 200, json={})
        if request.url.path.endswith("/machines"):
            return httpx.Response(200, json=[machine(binding, "machine-retry")])
        return httpx.Response(
            200,
            json=[
                {"id": "disk-one", "name": "pw_x", "state": "created"},
                {"id": "disk-orphan", "name": orphan, "state": "created"},
                {"id": "disk-other", "name": "pw_other", "state": "created"},
            ],
        )

    client, provider = await fly(monkeypatch, handle)
    async with client:
        await provider.destroy_workspace(binding)
    assert sorted(deleted) == ["disk-one", "disk-orphan", "machine-lost", "machine-retry"]


async def test_pausing_lost_compute_with_a_surviving_disk_counts_as_paused(monkeypatch):
    binding, calls = resource(), []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/volumes/disk-one"):
            return httpx.Response(200, json={"id": "disk-one", "state": "created"})
        return httpx.Response(404, json={})

    client, provider = await fly(monkeypatch, handle)
    async with client:
        result = await provider.pause_resource(host_for(binding))
    assert result.files_retained
    assert not any(path.endswith("/stop") for _, path in calls)


async def test_an_organization_machine_limit_is_an_actionable_error(monkeypatch):
    binding = resource(allocation_pending=True)

    def handle(request):
        if request.method == "POST" and request.url.path.endswith("/machines"):
            return httpx.Response(
                422, json={"error": "Your organization has reached its machine limit."}
            )
        if request.url.path.endswith("/volumes/disk-one"):
            return httpx.Response(200, json={"id": "disk-one", "region": "sin"})
        return httpx.Response(200, json=[])

    client, provider = await fly(monkeypatch, handle)
    async with client:
        with pytest.raises(WorkspaceUnavailable) as error:
            await provider.recreate(host_for(binding))
    assert error.value.code == "workbench_provider_capacity"
    assert error.value.retryable


def owner(rows=(), **repository):
    repo = Mock(**repository)
    repo.due.return_value = list(rows)
    runtime = SimpleNamespace(destroy_workspace=AsyncMock(), aclose=AsyncMock())
    service = SimpleNamespace(
        repository=repo,
        authorization=Mock(),
        context=Mock(),
        transport=Mock(),
        filesystem=Mock(),
        billing=SimpleNamespace(
            finish_session=AsyncMock(return_value=True),
            ensure_session=AsyncMock(),
            check_session=AsyncMock(),
            detach_session=AsyncMock(),
        ),
        channel_factory=Mock(return_value=SimpleNamespace(runtime=runtime)),
        providers=Mock(),
        channels=SimpleNamespace(invalidate=AsyncMock()),
        check=AsyncMock(),
    )
    lifecycle = WorkbenchLifecycle(service)
    lifecycle.preflight = AsyncMock(return_value=SimpleNamespace(name="fly"))
    return lifecycle, repo, runtime


def deleted_row(**overrides):
    row = dict(
        id="workspace",
        generation="generation",
        version=3,
        compute_epoch=1,
        state="retained",
        owner_deleted_at="2026-10-26T00:00:00+00:00",
        open_requested=True,
        resource=resource(),
        operation_id="op",
    )
    row.update(overrides)
    return row


async def test_a_deleted_owner_workspace_is_destroyed_and_retired():
    row = deleted_row()
    claimed = {**row, "state": "cleanup_pending", "version": 4}
    lifecycle, repo, runtime = owner(
        [row], reclaim_claim=Mock(return_value={"acquired": True, "workbench": claimed})
    )
    await lifecycle.reap()
    runtime.destroy_workspace.assert_awaited_once_with(row["resource"])
    repo.transition.assert_called_once_with(claimed, "retired")
    repo.idle.assert_not_called()  # Never paused or preserved again.


async def test_an_occupied_deleted_owner_workspace_waits():
    row = deleted_row()
    lifecycle, repo, runtime = owner(
        [row], reclaim_claim=Mock(return_value={"acquired": False, "workbench": row})
    )
    await lifecycle.reap()
    runtime.destroy_workspace.assert_not_awaited()
    repo.transition.assert_not_called()


def resuming(lifecycle):
    row = dict(
        id="workspace",
        user_id="user",
        project_id="project",
        generation="generation",
        version=5,
        compute_epoch=2,
        operation_id="op",
        state="resuming",
        is_default=True,
        resource=resource(),
        binding={"provider": "fly", "artifact": IMAGE},
    )
    runtime = SimpleNamespace(
        resume=AsyncMock(side_effect=WorkerLost("gone")), recreate=AsyncMock()
    )
    host = SimpleNamespace(resource=dict(row["resource"]), artifact=IMAGE, runtime=runtime)
    lifecycle.progress = AsyncMock()
    return row, host, runtime


async def test_lost_compute_is_recreated_on_the_same_disk_under_a_persisted_intent():
    lifecycle, repo, _ = owner()
    row, host, runtime = resuming(lifecycle)

    async def recreate(target):
        target.resource.update(resource_id="machine-new", allocation_pending=False)

    runtime.recreate.side_effect = recreate
    persisted = []

    def transition(current, state, value=None):
        persisted.append((state, dict(value or {})))  # As serialized at call time.
        return {**current, "version": current["version"] + 1}

    repo.transition.side_effect = transition
    await lifecycle._start_host(row, SimpleNamespace(org_id="org"), host)
    (first, intent), (second, confirmed) = persisted
    assert first == "resuming" and intent["allocation_pending"] is True
    assert intent["volume_id"] == "disk-one"
    assert second == "resuming" and confirmed["resource_id"] == "machine-new"
    assert confirmed["volume_id"] == "disk-one"


async def test_a_lost_disk_retires_the_generation_for_a_fresh_open():
    lifecycle, repo, _ = owner()
    row, host, runtime = resuming(lifecycle)
    runtime.recreate.side_effect = WorkspaceDiskLost("gone")
    repo.transition.side_effect = lambda current, state, value=None: {
        **current,
        "version": current["version"] + 1,
    }
    with pytest.raises(WorkspaceUnavailable) as error:
        await lifecycle._start_host(row, SimpleNamespace(org_id="org"), host)
    assert error.value.code == "workbench_disk_lost" and error.value.retryable
    repo.disk_lost.assert_called_once()
