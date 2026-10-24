"""Capabilities may use different hosts without changing recovery orchestration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform.access.adapters.agent.runtime.control.recovery import Recovery
from src.platform.access.adapters.agent.runtime.control.shared_recovery import SharedRecovery
from src.platform.workspace.filesystem.sandbox import SandboxFilesystem
from src.platform.access.adapters.agent.runtime.loop.pi import PiLoop
from src.platform.access.adapters.agent.runtime.loop.port import LoopProtocolError
from src.platform.workspace.execution.pi_worker import PiWorker
from src.platform.sandbox.store import InMemoryExecutionSessionStore


@pytest.mark.asyncio
async def test_filesystem_preparation_does_not_start_the_loop():
    worker = PiWorker(
        "execution", "project", provider="docker", store=InMemoryExecutionSessionStore()
    )
    worker._open_control = AsyncMock()
    worker.control = AsyncMock(
        return_value={
            "workspace_lifecycle": 1,
            "recovery_version": 1,
            "recovery_backend": "docker-volume",
        }
    )
    worker.snapshot = AsyncMock(return_value={"provider": "docker", "id": "snapshot"})
    worker.send = AsyncMock()
    workspace = {"target_ref": "refs/heads/main"}
    filesystem = SandboxFilesystem()
    filesystem.bind_recovery(worker, None)
    assert await filesystem.prepare(worker, workspace, resume=False) == {
        "provider": "docker",
        "id": "snapshot",
    }
    worker.send.assert_not_awaited()
    assert [call.args[0] for call in worker.control.await_args_list] == ["capabilities", "prepare"]
    await PiLoop().start(worker, {"prompt": "hello"})
    worker.send.assert_awaited_once_with({"type": "start", "config": {"prompt": "hello"}})
    # Starting the Loop does not repeat directory preparation or capture another snapshot.
    assert worker.control.await_count == 2
    worker.snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_loop_can_use_a_channel_separate_from_the_workspace():
    remote = SimpleNamespace(
        send=AsyncMock(), receive=AsyncMock(return_value={"type": "text", "delta": "hi"})
    )
    # No start/send/receive methods: a separate Loop must not use the resource channel.
    resource = object()
    loop = PiLoop(channel=remote)
    config = {"prompt": "hello", **loop.initial_state()}
    await loop.start(resource, config)
    await loop.send(resource, {"type": "model_end", "id": "model"})
    assert await loop.receive(resource) == {"type": "text", "delta": "hi"}
    assert [call.args[0] for call in remote.send.await_args_list] == [
        {"type": "start", "config": config},
        {"type": "model_end", "id": "model"},
    ]


def test_pi_retains_deployed_checkpoint_and_receipt_shapes():
    loop = PiLoop()
    state = {
        **loop.initial_state(),
        "entries": [
            {"message": {"role": "assistant", "content": [{"type": "text", "text": "old"}]}},
            {"message": {"role": "toolResult", "toolCallId": "done"}},
            {"message": {"role": "assistant", "content": [{"type": "text", "text": "new"}]}},
        ],
        "leaf_id": "leaf",
        "run_entry_start": 1,
        "workspace": {"tip": "commit"},
    }
    assert loop.checkpoint(state) == {
        key: state[key] for key in ("version", "pi_version", "entries", "leaf_id")
    }
    assert loop.continue_session(state) == {"entries": state["entries"], "leaf_id": "leaf"}
    assert loop.begin_run(state) == {"run_entry_start": 3}
    assert loop.completed_tools(state) == {"done"}
    assert loop.confirmed_text(state) == "new"
    result = {"content": [], "isError": True}
    receipt = loop.receipt_payload(result, {"sha256": "checkpoint"})
    assert receipt == {"pi_result": result, "checkpoint": {"sha256": "checkpoint"}}
    assert loop.receipt_result({"result": receipt}) == result


@pytest.mark.asyncio
async def test_pi_rejects_incompatible_operation_protocol_before_orchestration():
    channel = SimpleNamespace(
        receive=AsyncMock(
            return_value={
                "type": "ready",
                "pi_version": "0.85.1",
                "node_version": "v22.22.3",
                "workspace_version": 2,
                "operation_version": 0,
            }
        )
    )
    with pytest.raises(LoopProtocolError, match="worker_protocol_mismatch"):
        await PiLoop().receive(channel)


@pytest.mark.asyncio
async def test_recovery_uses_filesystem_binding_instead_of_resource_snapshot():
    channel = object()  # No control/snapshot capability on the execution resource.
    filesystem = SimpleNamespace(
        snapshot=AsyncMock(return_value={"external": "immutable-revision"}),
        finalize=AsyncMock(return_value={"changed": True, "tip": "commit"}),
    )
    recovery = Recovery(filesystem)
    state = {"workspace": {"target_ref": "refs/heads/main"}, "recovery": None}
    read = await recovery.after_tool(channel, state, {"name": "read"})
    assert read["recovery"] is None
    filesystem.snapshot.assert_not_awaited()
    # A failed Bash may have written files; it must still capture recovery material.
    written = await recovery.after_tool(channel, state, {"name": "bash", "is_error": True})
    assert written["recovery"] == {"external": "immutable-revision"}
    settled = await recovery.finalize(channel, written, {"id": "run"})
    assert settled["workspace"]["tip"] == "commit"
    filesystem.finalize.assert_awaited_once_with(channel, "Agent run run")
    assert filesystem.snapshot.await_count == 2


@pytest.mark.asyncio
async def test_shared_retry_uses_execution_binding_without_refinalizing_other_edits():
    channel = SimpleNamespace(resource={"workbench_id": "wb", "workbench_generation": "generation"})
    execution = SimpleNamespace(quiesce=AsyncMock())
    filesystem = SimpleNamespace(
        finalize=AsyncMock(side_effect=AssertionError("recaptured newer edits"))
    )
    state = {
        "workbench": {"id": "wb", "generation": "generation"},
        "recovery": None,
        "publication_candidate": "run",
    }
    assert (
        await SharedRecovery(execution, filesystem).finalize(channel, state, {"id": "run"}) == state
    )
    execution.quiesce.assert_awaited_once_with(channel, shared=True)
    filesystem.finalize.assert_not_awaited()
