"""Run publication selects its admitted workbench ref, not the display HEAD."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.access.adapters.agent.runtime.control.publication import (
    PUSH_ATTEMPTS,
    Publication,
)
from src.platform.workspace.filesystem.sandbox import SandboxFilesystem
from src.version_engine.read.ref_metadata import GitBranchBase, RefState
from tests.repository_hosting.unit.test_workbench_ref_publication import metadata


def run():
    return {
        "id": "run",
        "project_id": "project",
        "workbench_id": "workbench",
        "policy": {
            "readonly": False,
            "materialize": True,
            "path_prefix": "",
            "excludes": [],
            "workbench_target_ref": "refs/heads/workbench",
        },
    }


def base():
    return GitBranchBase.from_metadata(metadata(), target_ref="refs/heads/workbench").wire()


def test_capture_requires_admitted_workbench_ref_and_never_uses_display_default():
    transport = SimpleNamespace(describe=Mock(return_value=base()))
    value = Publication(transport, SandboxFilesystem()).capture(run(), "grant")
    assert value["workspace"]["target_ref"] == "refs/heads/workbench"
    transport.describe.assert_called_once_with(
        "project", "grant", target_ref="refs/heads/workbench"
    )
    missing = run()
    del missing["policy"]["workbench_target_ref"]
    transport.describe.reset_mock()
    with pytest.raises(PermissionError, match="Workbench publication target"):
        Publication(transport, SandboxFilesystem()).capture(missing, "grant")
    transport.describe.assert_not_called()


@pytest.mark.asyncio
async def test_retry_checks_selected_ref_and_pushes_only_original_candidate():
    checkpoint = {
        "base": base(),
        "workspace": {"tip": "c" * 40, "changed": True, "target_ref": "refs/heads/workbench"},
    }
    transport = SimpleNamespace(
        describe=Mock(return_value=base()), result=Mock(side_effect=[None, {"status": "committed"}])
    )
    worker = SimpleNamespace(control=AsyncMock(return_value={}))
    result = await Publication(transport, SandboxFilesystem()).publish(
        run(), checkpoint, "grant", worker
    )
    assert result["commit_id"] == "c" * 40
    transport.describe.assert_called_once_with(
        "project", "grant", target_ref="refs/heads/workbench"
    )
    worker.control.assert_awaited_once_with("push", {"candidate": "c" * 40})


@pytest.mark.asyncio
async def test_original_receipt_precedes_current_ref_lookup():
    transport = SimpleNamespace(
        describe=Mock(side_effect=AssertionError("looked at new HEAD")),
        result=Mock(return_value={"status": "committed"}),
    )
    worker = SimpleNamespace(control=AsyncMock())
    result = await Publication(transport, SandboxFilesystem()).publish(
        run(),
        {
            "base": base(),
            "workspace": {"tip": "c" * 40, "changed": True, "target_ref": "refs/heads/workbench"},
        },
        "grant",
        worker,
    )
    assert result["status"] == "committed"
    worker.control.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_target_must_still_match_admitted_workbench():
    transport = SimpleNamespace(describe=Mock(), result=Mock())
    worker = SimpleNamespace(control=AsyncMock())
    checkpoint = {
        "base": base(),
        "workspace": {"changed": True, "tip": "c" * 40, "target_ref": "refs/heads/other"},
    }
    with pytest.raises(PermissionError, match="Workbench publication target"):
        await Publication(transport, SandboxFilesystem()).publish(
            run(), checkpoint, "grant", worker
        )
    transport.result.assert_not_called()
    worker.control.assert_not_awaited()


def checkpoint():
    return {
        "base": base(),
        "workspace": {"tip": "c" * 40, "changed": True, "target_ref": "refs/heads/workbench"},
    }


@pytest.mark.asyncio
async def test_unchanged_task_does_not_push_or_query_remote():
    value = checkpoint()
    value["workspace"]["changed"] = False
    transport = SimpleNamespace(describe=Mock(), result=Mock())
    worker = SimpleNamespace(control=AsyncMock())
    assert await Publication(transport, SandboxFilesystem()).publish(
        run(), value, "grant", worker
    ) == {"status": "no_changes"}
    transport.describe.assert_not_called()
    transport.result.assert_not_called()
    worker.control.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_remote_tip_retains_candidate_without_automatic_pull_or_push():
    current = {**base(), "expected_oid": "d" * 40}
    transport = SimpleNamespace(describe=Mock(return_value=current), result=Mock(return_value=None))
    worker = SimpleNamespace(control=AsyncMock(return_value={"rejected": True}))
    value = checkpoint()
    result = await Publication(transport, SandboxFilesystem()).publish(run(), value, "grant", worker)
    assert result == {
        "status": "conflict", "code": "git_push_rejected",
        "commit_id": "c" * 40, "target_ref": "refs/heads/workbench",
    }
    assert value == checkpoint()
    # One ordinary fast-forward attempt: nothing pulls, rebases, resets or force-pushes.
    worker.control.assert_awaited_once_with(
        "push", {"candidate": "c" * 40, "remote_tip": "d" * 40}
    )


@pytest.mark.asyncio
async def test_non_fast_forward_after_preflight_preserves_original_candidate():
    transport = SimpleNamespace(describe=Mock(return_value=base()), result=Mock(return_value=None))
    worker = SimpleNamespace(control=AsyncMock(return_value={"rejected": True}))
    result = await Publication(transport, SandboxFilesystem()).publish(run(), checkpoint(), "grant", worker)
    assert result == {
        "status": "conflict", "code": "git_push_rejected",
        "commit_id": "c" * 40, "target_ref": "refs/heads/workbench",
    }
    worker.control.assert_awaited_once_with("push", {"candidate": "c" * 40})


@pytest.mark.asyncio
async def test_lost_push_ack_uses_original_receipt_without_repeating_push():
    transport = SimpleNamespace(
        describe=Mock(return_value=base()),
        result=Mock(side_effect=[None, {"status": "committed"}]),
    )
    worker = SimpleNamespace(control=AsyncMock(side_effect=ConnectionError("lost acknowledgement")))
    result = await Publication(transport, SandboxFilesystem()).publish(run(), checkpoint(), "grant", worker)
    assert result["status"] == "committed" and result["commit_id"] == "c" * 40
    assert transport.result.call_count == 2
    assert all(call.args == ("project", "grant", "run") for call in transport.result.call_args_list)
    worker.control.assert_awaited_once_with("push", {"candidate": "c" * 40})


@pytest.mark.asyncio
async def test_lost_push_ack_without_receipt_does_not_replay_task_or_publish_new_candidate():
    transport = SimpleNamespace(describe=Mock(return_value=base()), result=Mock(return_value=None))
    worker = SimpleNamespace(control=AsyncMock(side_effect=ConnectionError("lost acknowledgement")))
    with pytest.raises(ConnectionError, match="lost acknowledgement"):
        await Publication(transport, SandboxFilesystem()).publish(run(), checkpoint(), "grant", worker)
    worker.control.assert_awaited_once_with("push", {"candidate": "c" * 40})


def described(oid, generation=None):
    wire = base()
    selected = GitBranchBase.parse(wire)
    value = GitBranchBase(
        selected.object_format,
        generation or selected.generation,
        selected.ref_name,
        RefState(oid=oid),
        None,
    )
    return value.wire()


def overlapping(remote, *, results=(None,), push=None):
    """A Run that started at A while another task moved the branch to ``remote``."""
    checkpoint = {
        "base": described("a" * 40),
        "workspace": {"tip": "c" * 40, "changed": True, "target_ref": "refs/heads/workbench"},
    }
    transport = SimpleNamespace(
        describe=Mock(side_effect=remote), result=Mock(side_effect=list(results))
    )
    worker = SimpleNamespace(control=AsyncMock(side_effect=push or [{}]))
    return Publication(transport, SandboxFilesystem()), checkpoint, worker


@pytest.mark.asyncio
async def test_a_task_publishes_after_another_task_moved_their_shared_branch():
    publication, checkpoint, worker = overlapping(
        [described("b" * 40)], results=[None, {"status": "committed"}]
    )
    result = await publication.publish(run(), checkpoint, "grant", worker)
    assert result["status"] == "committed"
    assert GitBranchBase.parse(checkpoint["base"]).expected.oid == "b" * 40
    worker.control.assert_awaited_once_with(
        "push", {"candidate": "c" * 40, "remote_tip": "b" * 40}
    )


@pytest.mark.asyncio
async def test_a_task_whose_commit_is_already_the_branch_has_nothing_to_publish():
    publication, checkpoint, worker = overlapping([described("c" * 40)])
    result = await publication.publish(run(), checkpoint, "grant", worker)
    assert result["status"] == "no_changes"
    worker.control.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_task_published_inside_a_later_commit_has_nothing_to_publish():
    publication, checkpoint, worker = overlapping(
        [described("b" * 40)], results=[None, None], push=[{"contained": True}]
    )
    result = await publication.publish(run(), checkpoint, "grant", worker)
    assert result["status"] == "no_changes"


@pytest.mark.asyncio
async def test_diverged_history_is_still_a_conflict():
    remote = described("b" * 40)
    publication, checkpoint, worker = overlapping(
        [remote, remote], results=[None, None], push=[{"rejected": True}]
    )
    result = await publication.publish(run(), checkpoint, "grant", worker)
    assert (result["status"], result["code"]) == ("conflict", "git_push_rejected")
    worker.control.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_racing_publication_retries_against_the_moved_branch():
    first, second = described("b" * 40), described("d" * 40)
    publication, checkpoint, worker = overlapping(
        [first, second, second],
        results=[None, None, {"status": "committed"}],
        push=[{"rejected": True}, {}],
    )
    result = await publication.publish(run(), checkpoint, "grant", worker)
    assert result["status"] == "committed"
    assert worker.control.await_args_list[-1].args[1]["remote_tip"] == "d" * 40


@pytest.mark.asyncio
async def test_racing_publications_stop_after_bounded_attempts():
    moves = [described(c * 40) for c in "bdef1234"]
    publication, checkpoint, worker = overlapping(
        moves, results=[None] * 8, push=[{"rejected": True}] * 8
    )
    result = await publication.publish(run(), checkpoint, "grant", worker)
    assert (result["status"], result["code"]) == ("conflict", "git_push_rejected")
    assert worker.control.await_count == PUSH_ATTEMPTS


@pytest.mark.asyncio
async def test_a_replaced_repository_is_not_followed():
    publication, checkpoint, worker = overlapping([described("b" * 40, generation=9)])
    result = await publication.publish(run(), checkpoint, "grant", worker)
    assert (result["status"], result["code"]) == ("conflict", "cloud_branch_changed")
    worker.control.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_session_run_does_not_follow_a_moved_branch():
    session_run = {**run(), "workbench_id": None}
    publication, checkpoint, worker = overlapping([described("b" * 40)])
    result = await publication.publish(session_run, checkpoint, "grant", worker)
    assert (result["status"], result["code"]) == ("conflict", "cloud_branch_changed")
    worker.control.assert_not_awaited()
