"""Provider selection and private-machine transport; no hosted resource creation."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from src.platform.workspace.errors import WorkspaceError

from src.config import settings
from src.platform.access.adapters.agent.runtime.control.workspace import SessionWorkspace
from src.platform.workspace.execution.pi_worker import PiWorker
from src.platform.workspace.execution.providers.fly import FlyRuntimeProvider
from src.platform.workspace.execution.providers.registry import runtime_provider
from src.platform.workspace.execution.worker_port import WorkerDisconnected
from src.platform.workspace.service import WorkbenchAdmission


def test_interactive_chat_cannot_attach_an_automation_workspace():
    row = dict(user_id="user", project_id="project", owner_deleted_at=None, is_default=False)
    repository = SimpleNamespace(get=Mock(return_value=row))
    admission = WorkbenchAdmission(repository=repository, authorization=Mock())
    context = SimpleNamespace(user_id="user", project_id="project")
    with pytest.raises(WorkspaceError) as rejected:
        admission.admit(context, "automation-workspace")
    assert rejected.value.status_code == 409
    assert rejected.value.detail["code"] == "interactive_workspace_required"


def resource():
    return dict(
        provider="fly",
        app="original-app",
        resource_id="machine-one",
        project_id="project",
        workbench_id=str(uuid4()),
        workbench_generation=str(uuid4()),
        volume_id="disk-one",
        image_id="registry.fly.io/original-app@sha256:" + "a" * 64,
        allocation_pending=False,
    )


def machine(binding, state="started"):
    return dict(
        id=binding["resource_id"],
        state=state,
        config=dict(
            image=binding["image_id"],
            mounts=[
                dict(volume="disk-one", path="/data", encrypted=True, size_gb=1, name="pw_disk")
            ],
            metadata={
                k: binding[k] for k in ("workbench_id", "workbench_generation", "project_id")
            },
        ),
    )


def test_defaults_do_not_override_explicit_resource_provider(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROVIDER", "fly")
    assert runtime_provider().name == "fly"
    assert runtime_provider("e2b").name == "e2b"
    assert runtime_provider("docker").name == "docker"
    with pytest.raises(ValueError):
        runtime_provider("misspelled-provider")


@pytest.mark.asyncio
async def test_existing_session_uses_persisted_provider_and_artifact_after_switch(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROVIDER", "fly")
    repo = SimpleNamespace(
        workspace=Mock(
            return_value={
                "binding": {"provider": "e2b", "artifact": "original-template"},
                "resource": {"template": "original-template"},
            }
        )
    )
    factory = Mock()
    await SessionWorkspace(repo, None).worker(
        {"session_id": "session", "execution_id": "execution", "project_id": "project"}, factory
    )
    factory.assert_called_once_with(
        "execution",
        "project",
        provider="e2b",
        image="original-template",
        artifact="original-template",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["stopped", "suspended"])
async def test_fly_task_attachment_never_wakes_and_uses_persisted_app(monkeypatch, state):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_APP", "different-default")
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "backend-only")
    binding, requests = resource(), []

    def handle(request):
        requests.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer backend-only"
        return httpx.Response(200, json=machine(binding, state))

    async with httpx.AsyncClient(
        base_url="https://api.machines.dev", transport=httpx.MockTransport(handle)
    ) as client:
        provider = FlyRuntimeProvider(client=client)
        with pytest.raises(WorkerDisconnected):
            await provider.connect_task(
                SimpleNamespace(artifact=binding["image_id"]), binding, None
            )
    assert requests == [("GET", "/v1/apps/original-app/machines/machine-one")]


@pytest.mark.asyncio
async def test_fly_cleanup_keeps_disk_and_rejects_wrong_generation(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "backend-only")
    binding, requests = resource(), []
    row = machine(binding)

    def handle(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json=row if request.method == "GET" else {})

    async with httpx.AsyncClient(
        base_url="https://api.machines.dev", transport=httpx.MockTransport(handle)
    ) as client:
        provider = FlyRuntimeProvider(client=client)
        await provider.cleanup(binding)
        assert requests == [
            ("GET", "/v1/apps/original-app/machines/machine-one"),
            ("DELETE", "/v1/apps/original-app/machines/machine-one"),
        ]
        requests.clear()
        row["config"]["metadata"]["workbench_generation"] = str(uuid4())
        with pytest.raises(PermissionError):
            await provider.cleanup(binding)
        assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mounts",
    [
        [],
        [{"volume": "other-disk", "path": "/data"}],
        [{"volume": "disk-one", "path": "/other"}],
        [{"volume": "disk-one", "path": "/data"}, {"volume": "extra", "path": "/extra"}],
    ],
)
async def test_fly_rejects_changed_disk_binding_before_cleanup(monkeypatch, mounts):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "backend-only")
    binding, requests = resource(), []
    row = machine(binding)
    row["config"]["mounts"] = mounts

    def handle(request):
        requests.append(request.method)
        return httpx.Response(200, json=row)

    async with httpx.AsyncClient(
        base_url="https://api.machines.dev", transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(PermissionError, match="disk binding"):
            await FlyRuntimeProvider(client=client).cleanup(binding)
    assert requests == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_fly_retries_only_reads_after_transport_loss(monkeypatch, method):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "backend-only")
    requests = []

    def handle(request):
        requests.append(request.method)
        if len(requests) == 1:
            raise httpx.RemoteProtocolError("connection closed", request=request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        base_url="https://api.machines.dev", transport=httpx.MockTransport(handle)
    ) as client:
        provider = FlyRuntimeProvider(client=client)
        if method == "GET":
            assert await provider._request(resource(), method, "/machines") == {"ok": True}
            assert requests == ["GET", "GET"]
        else:
            with pytest.raises(httpx.RemoteProtocolError):
                await provider._request(resource(), method, "/machines")
            assert requests == [method]


@pytest.mark.asyncio
async def test_fly_ambiguous_allocation_never_creates_or_deletes(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "backend-only")
    binding, requests = resource(), []
    binding["allocation_pending"] = True

    def handle(request):
        requests.append(request.method)
        return httpx.Response(
            200, json=[machine(binding), {**machine(binding), "id": "second-machine"}]
        )

    async with httpx.AsyncClient(
        base_url="https://api.machines.dev", transport=httpx.MockTransport(handle)
    ) as client:
        provider = FlyRuntimeProvider(client=client)
        host = SimpleNamespace(resource=binding, artifact=binding["image_id"], restore_point=None)
        with pytest.raises(WorkerDisconnected, match="Ambiguous"):
            await provider.allocate(host)
    assert requests == ["GET"]


def test_e2b_allocation_intent_pins_template_before_create(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_E2B_TEMPLATE", "new-default")
    host = PiWorker(
        "execution", "project", provider="e2b", artifact="original-template", store=Mock()
    )
    assert host.resource["template"] == "original-template"
    assert host.resource["allocation_pending"] is True


@pytest.mark.asyncio
async def test_fly_allocation_keeps_files_on_private_persisted_disk(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "backend-only")
    binding = resource()
    binding["allocation_pending"] = True
    requests = []

    def handle(request):
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path.endswith("/volumes"):
            assert len(body["name"]) <= 30
            assert body["encrypted"] is True
            return httpx.Response(200, json={"id": "disk-one", "region": "sin"})
        if request.method == "POST" and request.url.path.endswith("/machines"):
            return httpx.Response(200, json={"id": "machine-created"})
        return httpx.Response(
            200, json={"state": "started"} if request.url.path.endswith("/wait") else []
        )

    async with httpx.AsyncClient(
        base_url="https://api.machines.dev", transport=httpx.MockTransport(handle)
    ) as client:
        provider = FlyRuntimeProvider(client=client)
        host = SimpleNamespace(resource=binding, artifact=binding["image_id"], restore_point=None)
        await provider.allocate(host)
    config = next(
        body["config"]
        for method, path, body in requests
        if method == "POST" and path.endswith("/machines")
    )
    assert config["services"] == []
    assert config["mounts"] == [{"volume": "disk-one", "path": "/data"}]
    assert config["image"] == binding["image_id"]
    assert config["metadata"]["workbench_generation"] == binding["workbench_generation"]
    assert "backend-only" not in str(config)
    assert host.resource["resource_id"] == "machine-created"
    assert host.resource["volume_id"] == "disk-one"
    assert not host.resource["allocation_pending"]


@pytest.mark.asyncio
async def test_docker_session_keeps_binding_tag_and_uses_resolved_image_for_attach():
    repo = SimpleNamespace(
        workspace=Mock(
            return_value={
                "binding": {"provider": "docker", "artifact": "old-tag"},
                "resource": {"image_id": "sha256:original"},
            }
        )
    )
    factory = Mock()
    await SessionWorkspace(repo, None).worker(
        {"session_id": "session", "execution_id": "execution", "project_id": "project"}, factory
    )
    factory.assert_called_once_with(
        "execution", "project", provider="docker", image="sha256:original", artifact="old-tag"
    )


@pytest.mark.asyncio
async def test_fly_reuses_management_client_and_closes_owned_client(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "backend-only")
    client = httpx.AsyncClient(
        base_url="https://api.machines.dev",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True})),
    )
    create = Mock(return_value=client)
    monkeypatch.setattr(httpx, "AsyncClient", create)
    provider = FlyRuntimeProvider()
    try:
        await provider._request(resource(), "GET", "/machines")
        await provider._request(resource(), "GET", "/volumes")
        create.assert_called_once()
    finally:
        await provider.aclose()
    assert client.is_closed


@pytest.mark.asyncio
async def test_fly_channel_uses_only_its_app_token_and_releases_tunnel(monkeypatch):
    import asyncio
    from pathlib import Path
    from unittest.mock import AsyncMock

    from src.platform.workspace.execution.providers.port import (
        RuntimeConnection,
        resource_identity,
    )

    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "backend-only")
    binding = resource()
    process = SimpleNamespace(
        stdin=SimpleNamespace(close=Mock()),
        wait=AsyncMock(return_value=0),
        stdout=SimpleNamespace(read=AsyncMock(return_value=b"")),
        stderr=SimpleNamespace(read=AsyncMock(return_value=b"")),
    )
    from src.platform.workspace.execution.providers.fly_transport import FlyTransport

    transport = FlyTransport("backend-only", "fly", binding["app"])
    lease = SimpleNamespace(process=process, release=Mock())
    transport.ssh = SimpleNamespace(open=AsyncMock(return_value=lease))
    transport.users = 1
    monkeypatch.setattr(
        "src.platform.workspace.execution.providers.fly.acquire_transport",
        AsyncMock(return_value=transport),
    )
    provider = FlyRuntimeProvider()
    provider.inspect = AsyncMock(side_effect=AssertionError("repeated management lookup"))
    host = SimpleNamespace(
        resource=binding,
        connection=RuntimeConnection(resource_identity(binding), "fdaa::1"),
        control_command=lambda: ["node", "controller.mjs"],
        _output=AsyncMock(),
        _disconnected=AsyncMock(),
        queue=asyncio.Queue(),
    )
    await provider.open_channel(host)
    env = transport.env
    assert transport.ssh.open.call_args.args[:2] == (binding, "fdaa::1")
    assert env["FLY_API_TOKEN"] == "backend-only"
    assert env["FLY_AGENT_ISOLATED"] == env["FLY_WIREGUARD_TOKEN_MODE"] == "1"
    directory = Path(env["FLY_CONFIG_DIR"])
    assert directory.exists() and directory != Path.home() / ".fly"
    assert (directory / "config.yml").read_text() == "{}\n"
    await provider.close_channel(host)
    await asyncio.gather(host.reader, host.stderr_reader)
    assert transport.users == 0 and directory.exists()
    transport.idle_task.cancel()
    transport.directory.cleanup()
    assert host.connection is None
    lease.release.assert_called_once()
