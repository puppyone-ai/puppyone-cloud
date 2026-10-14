"""Task attachment must not inherit the legacy computer owner's side effects."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.platform.scope_sandbox.execution.store import InMemoryExecutionSessionStore
from src.platform.scope_sandbox.execution.workbench_task import WorkbenchTask
from src.platform.scope_sandbox.execution.worker_port import WorkerDisconnected

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def no_provider_requests(monkeypatch):
    from e2b import AsyncSandbox

    from src.config import settings

    monkeypatch.setattr(settings, "CLOUD_AGENT_E2B_TEMPLATE", "fixture-template")
    for name in ("connect", "create", "kill"):
        monkeypatch.setattr(AsyncSandbox, name, AsyncMock(side_effect=AssertionError("task requested provider lifecycle")))


def worker(provider="docker"):
    value = WorkbenchTask(str(uuid4()), "project", workbench_id=str(uuid4()), generation=str(uuid4()),
                          task_uid=20000, provider=provider, image="fixture", store=InMemoryExecutionSessionStore())
    resource = {"provider": provider, "resource_id": "fixture-computer", "project_id": "project",
                "session_id": str(uuid4()), "image_id": "sha256:fixture", "template": value.artifact,
                "workbench_id": value.workbench_id, "workbench_generation": value.workbench_generation,
                "task_execution_id": value.task_execution_id, "task_uid": value.task_uid}
    return value, resource


@pytest.mark.parametrize("field", ["workbench_id", "workbench_generation", "project_id", "provider", "task_uid", "task_execution_id"])
async def test_wrong_or_missing_cleanup_binding_is_rejected_before_provider_access(field):
    value, resource = worker()
    value._docker = AsyncMock(side_effect=AssertionError("wrong resource binding reached provider"))
    resource.pop(field)
    with pytest.raises(PermissionError, match="binding"):
        await value.connect(resource)
    value._docker.assert_not_awaited()


async def test_task_connection_cannot_unpause_a_docker_computer():
    value, resource = worker()
    value._docker = AsyncMock(side_effect=lambda *args: json.dumps([{"Id": "sha256:fixture"}])
        if args[0] == "image" else json.dumps([{"Image": "sha256:fixture", "State": {"Running": True, "Paused": True}}]))
    with pytest.raises(WorkerDisconnected, match="lifecycle"):
        await value.connect(resource)
    assert all(call.args[0] != "unpause" for call in value._docker.await_args_list)


async def test_task_uses_its_execution_identity_not_the_resource_owner():
    value, resource = worker()
    value._docker = AsyncMock(side_effect=[json.dumps([{"Image": "sha256:fixture", "State": {"Running": True, "Paused": False}}]),
                                          json.dumps([{"Id": "sha256:fixture"}])])
    await value.connect(resource)
    assert value.execution_id == value.task_execution_id != resource["session_id"]
    assert value.resource == resource
    assert value.restore_point is None


async def test_e2b_task_requires_the_lifecycle_owners_existing_connection():
    value, resource = worker("e2b")
    with pytest.raises(PermissionError, match="lifecycle"):
        await value.connect(resource)
    connection = SimpleNamespace(sandbox_id=resource["resource_id"])
    await value.connect(resource, sandbox=connection)
    assert value.sandbox is connection
    with pytest.raises(PermissionError, match="binding"):
        await value.connect(resource, sandbox=SimpleNamespace(sandbox_id="another-computer"))


@pytest.mark.parametrize("protocol", [0, 1, 2])
async def test_task_requires_attach_only_protocol_before_preparing_or_starting(protocol):
    value, _ = worker()
    value._open_control = AsyncMock()
    value.control = AsyncMock(side_effect=[{"shared_workbench": protocol}, {}])
    value.send = AsyncMock()
    config = {"workspace": {"target_ref": "refs/heads/main"}}
    if protocol != 2:
        with pytest.raises(RuntimeError, match="protocol"):
            await value.start(config)
        value.control.assert_awaited_once_with("capabilities")
        value.send.assert_not_awaited()
    else:
        await value.start(config)
        assert value.control.await_args_list[1].args == (
            "prepare", {"target_ref": "refs/heads/main", "project_id": "project"})
        value.send.assert_awaited_once_with({"type": "start", "config": config})


async def test_legacy_resume_and_attach_are_not_task_capabilities():
    value, resource = worker()
    value._docker = AsyncMock(side_effect=AssertionError("task called provider lifecycle"))
    with pytest.raises(PermissionError, match="lifecycle"):
        await value.resume(resource)
    with pytest.raises(PermissionError, match="binding"):
        await WorkbenchTask.attach(resource, "project")
    value._docker.assert_not_awaited()


async def test_heartbeat_renews_task_control_not_provider_timeout():
    value, _ = worker("e2b")
    value.sandbox = SimpleNamespace(set_timeout=AsyncMock())
    value.control = AsyncMock(return_value={})
    value.reader = SimpleNamespace(done=lambda: False)
    await value.touch()
    value.control.assert_awaited_once_with("heartbeat")
    value.sandbox.set_timeout.assert_not_awaited()


async def test_cleanup_reconnects_a_task_controller_before_confirming_processes_dead():
    value, resource = worker("e2b")
    value.resource, value.attached = resource, True
    value.control = AsyncMock(return_value={})
    value._open_control = AsyncMock()
    value._disconnect_output = AsyncMock()
    await value.stop()
    value._open_control.assert_awaited_once()
    value.control.assert_awaited_once_with("park")
