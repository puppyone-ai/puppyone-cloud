"""Provider boundary contracts; no hosted service or credentials are used."""

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import settings
from src.platform.scope_sandbox.execution.pi_worker import PiWorker
from src.platform.scope_sandbox.execution.store import InMemoryExecutionSessionStore
from src.platform.scope_sandbox.execution.worker_port import WorkerLost


def worker(provider="e2b"):
    value = PiWorker(
        "new-execution", "project", provider=provider, store=InMemoryExecutionSessionStore()
    )
    value.resource["workspace_id"] = "session"
    return value


@pytest.mark.asyncio
async def test_e2b_creation_pauses_on_timeout_and_explicit_resume_keeps_identity(monkeypatch):
    from e2b import AsyncSandbox

    monkeypatch.setattr(settings, "CLOUD_AGENT_E2B_TEMPLATE", "fixed-template")
    sandbox = SimpleNamespace(sandbox_id="provider-id", pause=AsyncMock(return_value=True))
    create, connect = AsyncMock(return_value=sandbox), AsyncMock(return_value=sandbox)
    monkeypatch.setattr(AsyncSandbox, "create", create)
    monkeypatch.setattr(AsyncSandbox, "connect", connect)
    first = worker()
    resource = await first.create()
    assert "recovery_volume" not in resource
    assert create.call_args.kwargs["lifecycle"] == {"on_timeout": "pause", "auto_resume": False}
    assert create.call_args.kwargs["allow_internet_access"] is False
    first.control = AsyncMock(return_value={})
    await first.pause()
    first.control.assert_awaited_once_with("park")
    sandbox.pause.assert_awaited_once()
    next_worker = worker()
    await next_worker.resume(resource)
    assert next_worker.resource["resource_id"] == "provider-id"
    assert next_worker.reused and next_worker.restore_point is None
    assert create.await_count == 1
    assert connect.call_args.args == ("provider-id",)
    assert connect.call_args.kwargs["timeout"] == settings.RUNTIME_AGENT_TIMEOUT_SECONDS
    assert first.store.get(first.execution_id) is None  # Session registry owns it.


@pytest.mark.asyncio
async def test_pause_response_loss_retries_provider_without_restarting_control(monkeypatch):
    value = worker()
    value.control = AsyncMock(return_value={})
    value.sandbox = SimpleNamespace(pause=AsyncMock(side_effect=[TimeoutError(), False]))
    with pytest.raises(TimeoutError):
        await value.pause()
    await value.pause()
    value.control.assert_awaited_once_with("park")
    assert value.sandbox.pause.await_count == 2


@pytest.mark.asyncio
async def test_confirmed_missing_resource_restores_fresh_allocation_identity(monkeypatch):
    value = worker("docker")
    fresh = dict(value.resource)
    value._docker = AsyncMock(side_effect=RuntimeError("No such container: old-provider-id"))
    old = {**fresh, "resource_id": "old-provider-id", "session_id": "old-execution"}
    with pytest.raises(WorkerLost):
        await value.resume(old)
    assert value.resource == fresh and value.execution_id == "new-execution"
    assert not value.reused


@pytest.mark.asyncio
async def test_e2b_timeout_does_not_turn_into_cold_allocation(monkeypatch):
    from e2b import AsyncSandbox

    monkeypatch.setattr(settings, "CLOUD_AGENT_E2B_TEMPLATE", "fixed-template")
    monkeypatch.setattr(AsyncSandbox, "connect", AsyncMock(side_effect=TimeoutError()))
    value = worker()
    old = {
        **value.resource,
        "resource_id": "existing-id",
        "session_id": "old-execution",
        "template": "fixed-template",
    }
    with pytest.raises(TimeoutError):
        await value.resume(old)
    assert value.resource == old  # Cleanup/recovery still owns the uncertain resource.


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["e2b", "docker"])
@pytest.mark.parametrize(
    "capability",
    [{}, {"recovery_version": 0}, {"recovery_version": 1, "recovery_backend": "other"}],
)
async def test_incompatible_recovery_backend_rejects_before_prepare_or_model(provider, capability):
    value = worker(provider)
    value._open_control = AsyncMock()
    value.control = AsyncMock(return_value={"workspace_lifecycle": 1, **capability})
    value.send = AsyncMock()
    value.snapshot = AsyncMock()
    with pytest.raises(RuntimeError, match="recovery backend mismatch"):
        await value.start({"workspace": {}})
    value.control.assert_awaited_once_with("capabilities")
    value.send.assert_not_awaited()
    value.snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_e2b_restore_never_sends_native_snapshot_id_as_a_local_directory():
    value = worker()
    value.restore_point = {"provider": "e2b", "id": "native-snapshot", "project_id": "project"}
    value._open_control = AsyncMock()
    value.control = AsyncMock(
        return_value={
            "workspace_lifecycle": 1,
            "recovery_version": 1,
            "recovery_backend": "provider-snapshot",
        }
    )
    value.send = AsyncMock()
    value.snapshot = AsyncMock()
    await value.start({"workspace": {}, "resume_workspace": True})
    assert value.control.call_args.args[0] == "prepare"
    assert value.control.call_args.args[1]["restore"] is None
    assert value.recovery == value.restore_point
    value.snapshot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_e2b_snapshot_uses_only_provider_api_and_never_falls_back_to_local_copy(fails):
    value = worker()
    value.resource["template"] = "fixed-template"
    previous = {"provider": "e2b", "id": "previous"}
    value.recovery = previous
    value.control = AsyncMock()
    disconnect = AsyncMock()
    value.handle = SimpleNamespace(pid=42, disconnect=disconnect)
    value.reader = asyncio.create_task(asyncio.Event().wait())
    handle = SimpleNamespace(pid=42, wait=AsyncMock(side_effect=asyncio.Event().wait))
    snapshot = AsyncMock(return_value=SimpleNamespace(snapshot_id="native-snapshot"))
    if fails:
        snapshot.side_effect = RuntimeError("provider snapshot unavailable")
    value.sandbox = SimpleNamespace(
        create_snapshot=snapshot, commands=SimpleNamespace(
            connect=AsyncMock(return_value=handle), list=AsyncMock(return_value=[])
        )
    )
    try:
        if fails:
            with pytest.raises(RuntimeError, match="provider snapshot unavailable"):
                await value.snapshot()
            assert value.recovery == previous
            value.sandbox.commands.connect.assert_not_awaited()
        else:
            point = await value.snapshot()
            assert point == {
                "provider": "e2b",
                "id": "native-snapshot",
                "project_id": "project",
                "artifact": "fixed-template",
            }
            assert value.recovery == point
            value.sandbox.commands.connect.assert_awaited_once_with(
                42, timeout=0, on_stdout=value._output
            )
        disconnect.assert_awaited_once()
        value.sandbox.commands.list.assert_awaited_once_with(request_timeout=15)
        snapshot.assert_awaited_once()
        assert [call.args[0] for call in value.control.call_args_list] == ["freeze", "thaw"]
        assert not value.reconnecting
        assert "recovery_volume" not in value.resource
    finally:
        value.reconnecting = True
        value.reader.cancel()
        with suppress(asyncio.CancelledError):
            await value.reader
