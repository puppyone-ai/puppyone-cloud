import asyncio
import json
import threading
from pathlib import Path

import pytest

from src.platform.workspace.project_cleanup import (
    ProjectHostCleanupPort,
    ProjectHostResourceBusy,
)
from src.version_engine.infrastructure.file_lock import file_exclusive_lock


def _write_git_view(cache_root: Path, project_id: str, view_id: str) -> Path:
    view_dir = cache_root / project_id[:80] / view_id
    view_dir.mkdir(parents=True)
    (view_dir / "view.json").write_text(
        json.dumps({"project_id": project_id, "view_id": view_id}),
        encoding="utf-8",
    )
    (view_dir / "objects.pack").write_bytes(b"cache")
    return view_dir


def test_snapshot_rejects_path_traversal_before_touching_disk(tmp_path: Path):
    workspace_base = tmp_path / "workspace"
    git_cache = tmp_path / "git-cache"
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    port = ProjectHostCleanupPort(
        workspace_base_dir=workspace_base,
        git_view_cache_dir=git_cache,
    )

    with pytest.raises(ValueError, match="single safe storage path segment"):
        port.snapshot("../outside")

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not workspace_base.exists()
    assert not git_cache.exists()


@pytest.mark.asyncio
async def test_missing_host_resources_are_idempotent(tmp_path: Path):
    port = ProjectHostCleanupPort(
        workspace_base_dir=tmp_path / "workspace",
        git_view_cache_dir=tmp_path / "git-cache",
    )
    snapshot = port.snapshot("project-1")

    first = await port.delete(snapshot)
    second = await port.delete(snapshot)

    assert first.deleted_paths == 0
    assert second.deleted_paths == 0
    assert first.missing_paths == 2
    assert second.missing_paths == 2
    assert port.verify(snapshot).complete is True


@pytest.mark.asyncio
async def test_cleanup_removes_only_exact_project_including_colliding_git_dir(
    tmp_path: Path,
):
    target_project = "a" * 80
    other_project = f"{target_project}b"
    workspace_base = tmp_path / "workspace"
    git_cache = tmp_path / "git-cache"

    target_lower = workspace_base / "lower" / target_project
    other_lower = workspace_base / "lower" / other_project
    target_workspace = workspace_base / "workspaces" / target_project / "agent-1"
    other_workspace = workspace_base / "workspaces" / other_project / "agent-2"
    for path in (target_lower, other_lower, target_workspace, other_workspace):
        path.mkdir(parents=True)
        (path / "keep.txt").write_text(path.name, encoding="utf-8")

    target_view = _write_git_view(git_cache, target_project, "1" * 64)
    other_view = _write_git_view(git_cache, other_project, "2" * 64)
    port = ProjectHostCleanupPort(
        workspace_base_dir=workspace_base,
        git_view_cache_dir=git_cache,
    )

    snapshot = port.snapshot(target_project)
    assert tuple(handle.view_dir for handle in snapshot.git_views) == (target_view,)

    await port.delete(snapshot)

    assert port.verify(snapshot).complete is True
    assert not target_lower.exists()
    assert not target_workspace.parent.exists()
    assert not target_view.exists()
    assert (other_lower / "keep.txt").exists()
    assert (other_workspace / "keep.txt").exists()
    assert (other_view / "objects.pack").read_bytes() == b"cache"


@pytest.mark.asyncio
async def test_leftover_legacy_workspace_directories_are_scrubbed(tmp_path: Path):
    # Retired host-local Agent workspaces left lower/ and workspaces/ trees on
    # older hosts. A fresh port removes them by path, without any registry.
    workspace_base = tmp_path / "workspace"
    lower = workspace_base / "lower" / "project-1"
    workspace = workspace_base / "workspaces" / "project-1" / "agent-1"
    for path in (lower, workspace):
        path.mkdir(parents=True)
        (path / "file.txt").write_text("content", encoding="utf-8")

    port = ProjectHostCleanupPort(
        workspace_base_dir=workspace_base,
        git_view_cache_dir=tmp_path / "git-cache",
    )
    snapshot = port.snapshot("project-1")
    result = await port.delete(snapshot)

    assert result.deleted_paths == 2
    assert not workspace.parent.exists()
    assert not lower.exists()
    assert port.verify(snapshot).complete is True


@pytest.mark.asyncio
async def test_active_git_view_is_retryable_and_not_deleted(tmp_path: Path):
    git_cache = tmp_path / "git-cache"
    view_dir = _write_git_view(git_cache, "project-1", "3" * 64)
    port = ProjectHostCleanupPort(
        workspace_base_dir=tmp_path / "workspace",
        git_view_cache_dir=git_cache,
    )
    snapshot = port.snapshot("project-1")
    lock_path = snapshot.git_views[0].lock_path

    with (
        file_exclusive_lock(lock_path),
        pytest.raises(ProjectHostResourceBusy, match="Git view is active"),
    ):
        await port.delete(snapshot)

    assert view_dir.exists()
    assert port.verify(snapshot).complete is False


@pytest.mark.asyncio
async def test_cancellation_waits_for_physical_host_deletion(
    tmp_path: Path,
    monkeypatch,
):
    port = ProjectHostCleanupPort(
        workspace_base_dir=tmp_path / "workspace",
        git_view_cache_dir=tmp_path / "git-cache",
    )
    snapshot = port.snapshot("project-1")
    started = threading.Event()
    finish = threading.Event()

    def blocking_delete(_snapshot):
        started.set()
        finish.wait(timeout=5)
        return 0, 2

    monkeypatch.setattr(port, "_delete_sync", blocking_delete)
    task = asyncio.create_task(port.delete(snapshot))
    assert await asyncio.to_thread(started.wait, 1)

    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_partial_git_view_is_removed_under_project_lock(tmp_path: Path):
    project_id = "project-1"
    git_cache = tmp_path / "git-cache"
    partial = git_cache / project_id / ("b" * 64)
    partial.mkdir(parents=True)
    (partial / "objects").mkdir()
    (partial / "objects" / "partial-pack").write_bytes(b"secret")
    port = ProjectHostCleanupPort(
        workspace_base_dir=tmp_path / "workspace",
        git_view_cache_dir=git_cache,
    )

    snapshot = port.snapshot(project_id)
    assert snapshot.unresolved_git_entries == (partial,)

    await port.delete(snapshot)

    assert not partial.exists()
    assert port.verify(snapshot).complete is True
