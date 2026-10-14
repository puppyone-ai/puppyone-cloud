"""Conversation recovery must not restore or snapshot the shared filesystem."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform.access.adapters.agent.runtime.shared_recovery import SharedRecovery


def state():
    value = {"workbench": {"id": "workbench", "generation": "generation"},
             "entries": ["conversation"], "workspace": {"target_ref": "refs/heads/main"}, "recovery": None}
    worker = SimpleNamespace(
        resource={"workbench_id": "workbench", "workbench_generation": "generation"},
        snapshot=AsyncMock(side_effect=AssertionError("shared filesystem snapshot")),
        control=AsyncMock(return_value={"target_ref": "refs/heads/main", "tip": "candidate", "changed": True}),
    )
    return value, worker


@pytest.mark.asyncio
@pytest.mark.parametrize("name,error", [("write", False), ("bash", False), ("bash", True), ("read", False)])
async def test_tool_receipts_change_only_conversation_checkpoint(name, error):
    value, worker = state()
    result = await SharedRecovery().after_tool(worker, value, {"is_error": error, "name": name})
    assert result == {**value, "reason": "after_tool"}
    worker.snapshot.assert_not_awaited()
    worker.control.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalization_keeps_native_candidate_without_filesystem_restore_point():
    value, worker = state()
    value["reason"] = "settled"  # Pi settled; service Git has not finalized yet.
    result = await SharedRecovery().finalize(worker, value, {"id": "run", "policy": {}})
    assert result["reason"] == "settled" and result["workspace"]["tip"] == "candidate"
    assert result["recovery"] is None
    worker.snapshot.assert_not_awaited()
    worker.control.assert_awaited_once()
    # Retrying a publication must not incorporate somebody else's newer edits.
    assert await SharedRecovery().finalize(worker, result, {"id": "run", "policy": {}}) == result
    assert [call.args[0] for call in worker.control.await_args_list] == ["finalize", "quiesce"]
    with pytest.raises(PermissionError):
        await SharedRecovery().finalize(worker, result, {"id": "other-run", "policy": {}})


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "workbench", "legacy_snapshot"])
async def test_incompatible_checkpoint_fails_before_touching_live_files(change):
    value, worker = state()
    if change == "legacy_snapshot":
        value["recovery"] = {"snapshot_id": "old-whole-computer-copy"}
    else:
        worker.resource["workbench_generation" if change == "generation" else "workbench_id"] = "other"
    with pytest.raises(PermissionError):
        await SharedRecovery().finalize(worker, value, {"id": "run", "policy": {}})
    worker.control.assert_not_awaited()
    worker.snapshot.assert_not_awaited()
