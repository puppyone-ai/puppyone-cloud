"""Provider selection and private-machine transport; no hosted resource creation."""

from types import SimpleNamespace
import json
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from src.config import settings
from src.platform.scope_sandbox.execution.providers.registry import runtime_provider
from src.platform.scope_sandbox.execution.providers.fly import FlyRuntimeProvider
from src.platform.scope_sandbox.execution.pi_worker import PiWorker
from src.platform.scope_sandbox.execution.worker_port import WorkerDisconnected
from src.platform.access.adapters.agent.runtime.control.workspace import SessionWorkspace
from src.platform.scope_sandbox.workbench_service import WorkbenchAdmission
from fastapi import HTTPException


def test_interactive_chat_cannot_attach_an_automation_workspace():
    row = dict(user_id="user", project_id="project", owner_deleted_at=None, is_default=False)
    repository = SimpleNamespace(get=Mock(return_value=row))
    admission = WorkbenchAdmission(repository=repository, authorization=Mock())
    context = SimpleNamespace(user_id="user", project_id="project")
    with pytest.raises(HTTPException) as rejected:
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
            mounts=[dict(volume="disk-one", path="/data")],
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
