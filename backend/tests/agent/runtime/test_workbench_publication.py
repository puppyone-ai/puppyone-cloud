"""Run publication selects its admitted workbench ref, not the display HEAD."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.access.adapters.agent.runtime.control.publication import Publication
from src.platform.workspace.filesystem.sandbox import SandboxFilesystem
from src.version_engine.read.ref_metadata import GitBranchBase
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
