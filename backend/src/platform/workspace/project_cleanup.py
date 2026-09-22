"""Strict cleanup port for Project-owned host workspace and cache state.

The deletion worker owns orchestration.  This module owns only the host-side
contract: snapshot exact handles, delete those handles idempotently, and then
verify the Project has no remaining deterministic workspace/cache state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from src.platform.workspace.paths import (
    absolute_path,
    project_child,
    validate_storage_segment,
)
from src.platform.workspace.provider import (
    WorkspaceProvider,
    get_active_workspace_provider,
)
from src.version_engine.adapters.git._filelock import try_file_exclusive_lock

_VIEW_ID = re.compile(r"[0-9a-f]{64}")
_VIEW_LOCK = re.compile(r"\.[0-9a-f]{64}\.lock")


class ProjectHostCleanupError(RuntimeError):
    """Base error for a host cleanup attempt that must be retried or failed."""


class ProjectHostResourceBusy(ProjectHostCleanupError):
    """A target Git view is still in use and could not be deleted safely."""


class ProjectHostResourceSafetyError(ProjectHostCleanupError):
    """A path or ownership proof did not match the snapshotted contract."""


@dataclass(frozen=True, slots=True)
class ProjectHostPathHandle:
    kind: Literal["lower_cache", "workspace_tree"]
    project_id: str
    root: Path
    path: Path

    def validate(self) -> None:
        if self.kind not in {"lower_cache", "workspace_tree"}:
            raise ProjectHostResourceSafetyError("host resource kind is invalid")
        validate_storage_segment(self.project_id, label="project_id")
        normalized_root = absolute_path(self.root)
        if self.root != normalized_root:
            raise ProjectHostResourceSafetyError("host resource root is not absolute")
        if self.root.is_symlink():
            raise ProjectHostResourceSafetyError("host resource root is a symlink")
        if self.path != project_child(normalized_root, self.project_id):
            raise ProjectHostResourceSafetyError(
                f"{self.kind} handle is not the exact Project child"
            )


@dataclass(frozen=True, slots=True)
class GitViewCacheHandle:
    project_id: str
    cache_root: Path
    project_dir: Path
    view_dir: Path
    lock_path: Path
    metadata_digest: str

    def validate(self) -> None:
        validate_storage_segment(self.project_id, label="project_id")
        normalized_root = absolute_path(self.cache_root)
        expected_project_dir = normalized_root / self.project_id[:80]
        if self.cache_root != normalized_root or self.project_dir != expected_project_dir:
            raise ProjectHostResourceSafetyError("Git cache handle escaped its configured root")
        if not _VIEW_ID.fullmatch(self.view_dir.name):
            raise ProjectHostResourceSafetyError("Git cache handle has an invalid view id")
        if self.view_dir.parent != expected_project_dir:
            raise ProjectHostResourceSafetyError("Git view handle escaped its Project directory")
        if self.lock_path != expected_project_dir / f".{self.view_dir.name}.lock":
            raise ProjectHostResourceSafetyError("Git view handle has an invalid lock path")
        if not _VIEW_ID.fullmatch(self.metadata_digest):
            raise ProjectHostResourceSafetyError(
                "Git cache handle has an invalid metadata digest"
            )


@dataclass(frozen=True, slots=True)
class ProjectHostResourceSnapshot:
    project_id: str
    lower_cache: ProjectHostPathHandle
    workspace_tree: ProjectHostPathHandle
    git_views: tuple[GitViewCacheHandle, ...]
    unresolved_git_entries: tuple[Path, ...] = ()

    @property
    def handles(self) -> tuple[ProjectHostPathHandle | GitViewCacheHandle, ...]:
        return (self.lower_cache, self.workspace_tree, *self.git_views)

    def validate(self) -> None:
        validate_storage_segment(self.project_id, label="project_id")
        self.lower_cache.validate()
        self.workspace_tree.validate()
        if (
            self.lower_cache.project_id != self.project_id
            or self.workspace_tree.project_id != self.project_id
        ):
            raise ProjectHostResourceSafetyError("host snapshot mixes Project ownership")
        for handle in self.git_views:
            handle.validate()
            if handle.project_id != self.project_id:
                raise ProjectHostResourceSafetyError("Git snapshot mixes Project ownership")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "project_id": self.project_id,
            "lower_cache": {
                "kind": self.lower_cache.kind,
                "project_id": self.lower_cache.project_id,
                "root": str(self.lower_cache.root),
                "path": str(self.lower_cache.path),
            },
            "workspace_tree": {
                "kind": self.workspace_tree.kind,
                "project_id": self.workspace_tree.project_id,
                "root": str(self.workspace_tree.root),
                "path": str(self.workspace_tree.path),
            },
            "git_views": [
                {
                    "project_id": handle.project_id,
                    "cache_root": str(handle.cache_root),
                    "project_dir": str(handle.project_dir),
                    "view_dir": str(handle.view_dir),
                    "lock_path": str(handle.lock_path),
                    "metadata_digest": handle.metadata_digest,
                }
                for handle in self.git_views
            ],
            "unresolved_git_entries": [
                str(path) for path in self.unresolved_git_entries
            ],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProjectHostResourceSnapshot:
        try:
            project_id = str(value["project_id"])
            lower = value["lower_cache"]
            workspace = value["workspace_tree"]
            raw_views = value.get("git_views", [])
            raw_unresolved = value.get("unresolved_git_entries", [])
            if (
                not isinstance(lower, dict)
                or not isinstance(workspace, dict)
                or not isinstance(raw_views, list)
                or not isinstance(raw_unresolved, list)
            ):
                raise TypeError("snapshot resources have invalid shapes")
            snapshot = cls(
                project_id=project_id,
                lower_cache=ProjectHostPathHandle(
                    kind=str(lower["kind"]),  # type: ignore[arg-type]
                    project_id=str(lower["project_id"]),
                    root=Path(str(lower["root"])),
                    path=Path(str(lower["path"])),
                ),
                workspace_tree=ProjectHostPathHandle(
                    kind=str(workspace["kind"]),  # type: ignore[arg-type]
                    project_id=str(workspace["project_id"]),
                    root=Path(str(workspace["root"])),
                    path=Path(str(workspace["path"])),
                ),
                git_views=tuple(
                    GitViewCacheHandle(
                        project_id=str(item["project_id"]),
                        cache_root=Path(str(item["cache_root"])),
                        project_dir=Path(str(item["project_dir"])),
                        view_dir=Path(str(item["view_dir"])),
                        lock_path=Path(str(item["lock_path"])),
                        metadata_digest=str(item["metadata_digest"]),
                    )
                    for item in raw_views
                    if isinstance(item, dict)
                ),
                unresolved_git_entries=tuple(
                    Path(str(path)) for path in raw_unresolved
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectHostResourceSafetyError(
                "persisted host cleanup snapshot is invalid"
            ) from exc
        if len(snapshot.git_views) != len(raw_views):
            raise ProjectHostResourceSafetyError(
                "persisted Git cleanup handles are invalid"
            )
        snapshot.validate()
        return snapshot


@dataclass(frozen=True, slots=True)
class ProjectHostDeletionResult:
    deleted_paths: int
    missing_paths: int
    forgotten_workspaces: int


@dataclass(frozen=True, slots=True)
class ProjectHostCleanupVerification:
    complete: bool
    remaining_paths: tuple[Path, ...]
    remaining_git_views: tuple[Path, ...]
    unresolved_git_entries: tuple[Path, ...]
    live_workspace_agents: tuple[str, ...]


class ProjectHostCleanupPort:
    """Enumerate, remove, and verify exact Project-owned host resources."""

    def __init__(
        self,
        *,
        workspace_base_dir: str | os.PathLike[str] | None = None,
        git_view_cache_dir: str | os.PathLike[str] | None = None,
        workspace_provider: WorkspaceProvider | None = None,
    ) -> None:
        if workspace_base_dir is None or git_view_cache_dir is None:
            from src.config import settings

            if workspace_base_dir is None:
                workspace_base_dir = settings.WORKSPACE_BASE_DIR
            if git_view_cache_dir is None:
                git_view_cache_dir = (
                    os.getenv("PUPPYONE_GIT_VIEW_CACHE_DIR", "").strip()
                    or str(settings.GIT_VIEW_CACHE_DIR)
                )
        self._workspace_base = absolute_path(workspace_base_dir)
        self._git_cache_root = absolute_path(git_view_cache_dir)
        self._workspace_provider = workspace_provider

    def snapshot(self, project_id: str) -> ProjectHostResourceSnapshot:
        """Snapshot only deterministic paths and exact Git ownership proofs."""

        project_id = validate_storage_segment(project_id, label="project_id")
        lower_root = self._workspace_base / "lower"
        workspace_root = self._workspace_base / "workspaces"
        git_views, unresolved = self._snapshot_git_views(project_id)
        snapshot = ProjectHostResourceSnapshot(
            project_id=project_id,
            lower_cache=ProjectHostPathHandle(
                kind="lower_cache",
                project_id=project_id,
                root=lower_root,
                path=project_child(lower_root, project_id),
            ),
            workspace_tree=ProjectHostPathHandle(
                kind="workspace_tree",
                project_id=project_id,
                root=workspace_root,
                path=project_child(workspace_root, project_id),
            ),
            git_views=git_views,
            unresolved_git_entries=unresolved,
        )
        snapshot.validate()
        return snapshot

    async def delete(
        self,
        snapshot: ProjectHostResourceSnapshot,
    ) -> ProjectHostDeletionResult:
        """Delete a snapshot and wait for real filesystem work on cancellation."""

        self._validate_configured_snapshot(snapshot)
        task = asyncio.create_task(asyncio.to_thread(self._delete_sync, snapshot))
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            self._forget_project_workspaces(snapshot.project_id)
            raise cancelled
        forgotten = self._forget_project_workspaces(snapshot.project_id)
        return ProjectHostDeletionResult(
            deleted_paths=result[0],
            missing_paths=result[1],
            forgotten_workspaces=forgotten,
        )

    def verify(
        self,
        snapshot: ProjectHostResourceSnapshot,
    ) -> ProjectHostCleanupVerification:
        """Re-scan the exact Project so post-snapshot cache creation is visible."""

        self._validate_configured_snapshot(snapshot)
        remaining_paths = tuple(
            handle.path
            for handle in (snapshot.lower_cache, snapshot.workspace_tree)
            if os.path.lexists(handle.path)
        )
        fresh_views, unresolved = self._snapshot_git_views(snapshot.project_id)
        provider = self._provider_for_base()
        live_agents: tuple[str, ...] = ()
        if provider is not None:
            live_agents = tuple(
                sorted(
                    info.agent_id
                    for info in provider.snapshot_project_workspaces(snapshot.project_id)
                )
            )
        remaining_git_views = tuple(handle.view_dir for handle in fresh_views)
        return ProjectHostCleanupVerification(
            complete=not (
                remaining_paths
                or remaining_git_views
                or unresolved
                or live_agents
            ),
            remaining_paths=remaining_paths,
            remaining_git_views=remaining_git_views,
            unresolved_git_entries=unresolved,
            live_workspace_agents=live_agents,
        )

    def restore(self, value: dict[str, Any]) -> ProjectHostResourceSnapshot:
        snapshot = ProjectHostResourceSnapshot.from_dict(value)
        self._validate_configured_snapshot(snapshot)
        return snapshot

    def _validate_configured_snapshot(
        self,
        snapshot: ProjectHostResourceSnapshot,
    ) -> None:
        snapshot.validate()
        expected_lower = self._workspace_base / "lower"
        expected_workspaces = self._workspace_base / "workspaces"
        if (
            snapshot.lower_cache.root != expected_lower
            or snapshot.workspace_tree.root != expected_workspaces
            or any(
                handle.cache_root != self._git_cache_root
                for handle in snapshot.git_views
            )
            or any(
                path.parent != self._git_cache_root / snapshot.project_id[:80]
                for path in snapshot.unresolved_git_entries
            )
        ):
            raise ProjectHostResourceSafetyError(
                "persisted host cleanup snapshot does not match configured roots"
            )

    def _delete_sync(
        self,
        snapshot: ProjectHostResourceSnapshot,
    ) -> tuple[int, int]:
        deleted = 0
        missing = 0
        for handle in snapshot.git_views:
            if self._delete_git_view(handle):
                deleted += 1
            else:
                missing += 1

        # A process can crash after creating a hash-addressed view directory
        # but before publishing view.json. Under a non-truncated Project cache
        # directory the path itself is an exact ownership proof; acquire the
        # ordinary per-view lock before removing that partial state.
        for entry in snapshot.unresolved_git_entries:
            if self._delete_partial_git_view(snapshot.project_id, entry):
                deleted += 1
            else:
                missing += 1

        for handle in (snapshot.workspace_tree, snapshot.lower_cache):
            if self._delete_project_path(handle):
                deleted += 1
            else:
                missing += 1

        return deleted, missing

    def _delete_partial_git_view(self, project_id: str, entry: Path) -> bool:
        validate_storage_segment(project_id, label="project_id")
        project_dir = self._git_cache_root / project_id[:80]
        if len(project_id) >= 80 or entry.parent != project_dir:
            raise ProjectHostResourceSafetyError(
                "unresolved Git entry lacks an unambiguous Project owner"
            )
        if not _VIEW_ID.fullmatch(entry.name):
            raise ProjectHostResourceSafetyError(
                f"unresolved Git entry is not a view directory: {entry}"
            )
        lock_path = project_dir / f".{entry.name}.lock"
        if lock_path.is_symlink():
            raise ProjectHostResourceSafetyError(
                f"Git view lock is a symlink: {lock_path}"
            )
        with try_file_exclusive_lock(lock_path) as acquired:
            if not acquired:
                raise ProjectHostResourceBusy(f"Git view is active: {entry}")
            if not os.path.lexists(entry):
                return False
            if entry.is_symlink() or not entry.is_dir():
                raise ProjectHostResourceSafetyError(
                    f"partial Git view is not a real directory: {entry}"
                )
            shutil.rmtree(entry)
        return True

    def _delete_git_view(self, handle: GitViewCacheHandle) -> bool:
        handle.validate()
        if not os.path.lexists(handle.view_dir):
            return False
        if handle.view_dir.is_symlink() or not handle.view_dir.is_dir():
            raise ProjectHostResourceSafetyError(
                f"Git view is not a real directory: {handle.view_dir}"
            )
        if handle.lock_path.is_symlink():
            raise ProjectHostResourceSafetyError(
                f"Git view lock is a symlink: {handle.lock_path}"
            )

        with try_file_exclusive_lock(handle.lock_path) as acquired:
            if not acquired:
                raise ProjectHostResourceBusy(f"Git view is active: {handle.view_dir}")
            if not os.path.lexists(handle.view_dir):
                return False
            if handle.view_dir.is_symlink() or not handle.view_dir.is_dir():
                raise ProjectHostResourceSafetyError(
                    f"Git view ownership path changed after snapshot: {handle.view_dir}"
                )
            raw_metadata = self._read_view_metadata_bytes(handle.view_dir)
            if raw_metadata is None:
                raise ProjectHostResourceSafetyError(
                    f"Git view ownership changed after snapshot: {handle.view_dir}"
                )
            if hashlib.sha256(raw_metadata).hexdigest() != handle.metadata_digest:
                raise ProjectHostResourceBusy(
                    f"Git view changed after snapshot: {handle.view_dir}"
                )
            try:
                metadata = json.loads(raw_metadata)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProjectHostResourceSafetyError(
                    f"Git view metadata is invalid: {handle.view_dir}"
                ) from exc
            if (
                not isinstance(metadata, dict)
                or metadata.get("project_id") != handle.project_id
                or metadata.get("view_id") != handle.view_dir.name
            ):
                raise ProjectHostResourceSafetyError(
                    f"Git view ownership changed after snapshot: {handle.view_dir}"
                )
            shutil.rmtree(handle.view_dir)
        return True

    def _delete_project_path(self, handle: ProjectHostPathHandle) -> bool:
        handle.validate()
        if not os.path.lexists(handle.path):
            return False
        if handle.path.is_symlink() or not handle.path.is_dir():
            handle.path.unlink()
            return True
        if handle.kind == "workspace_tree":
            self._unmount_project_workspaces(handle.path)
        shutil.rmtree(handle.path)
        return True

    def _unmount_project_workspaces(self, project_workspace_root: Path) -> None:
        for workspace_root in project_workspace_root.iterdir():
            if workspace_root.is_symlink() or not workspace_root.is_dir():
                continue
            try:
                validate_storage_segment(workspace_root.name, label="agent_id")
            except ValueError as exc:
                raise ProjectHostResourceSafetyError(
                    f"workspace has an invalid Agent segment: {workspace_root}"
                ) from exc
            merged = workspace_root / "merged"
            if merged.is_symlink() or not os.path.lexists(merged):
                continue
            try:
                is_mount = merged.is_mount()
            except OSError as exc:
                raise ProjectHostCleanupError(
                    f"unable to inspect workspace mount {merged}: {exc}"
                ) from exc
            if not is_mount:
                continue
            try:
                result = subprocess.run(
                    ["umount", str(merged)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ProjectHostResourceBusy(
                    f"unable to unmount Project workspace {merged}: {exc}"
                ) from exc
            if result.returncode != 0:
                detail = result.stderr.strip() or f"exit {result.returncode}"
                raise ProjectHostResourceBusy(
                    f"unable to unmount Project workspace {merged}: {detail}"
                )

    def _snapshot_git_views(
        self,
        project_id: str,
    ) -> tuple[tuple[GitViewCacheHandle, ...], tuple[Path, ...]]:
        project_dir = self._git_cache_root / project_id[:80]
        if not os.path.lexists(project_dir):
            return (), ()
        if project_dir.is_symlink() or not project_dir.is_dir():
            raise ProjectHostResourceSafetyError(
                f"Git Project cache is not a real directory: {project_dir}"
            )

        handles: list[GitViewCacheHandle] = []
        unresolved: list[Path] = []
        for entry in sorted(project_dir.iterdir(), key=lambda item: item.name):
            if not _VIEW_ID.fullmatch(entry.name):
                if not _VIEW_LOCK.fullmatch(entry.name) and len(project_id) < 80:
                    unresolved.append(entry)
                continue
            if entry.is_symlink() or not entry.is_dir():
                if len(project_id) < 80:
                    unresolved.append(entry)
                continue
            raw_metadata = self._read_view_metadata_bytes(entry)
            if raw_metadata is None:
                if len(project_id) < 80:
                    unresolved.append(entry)
                continue
            try:
                metadata = json.loads(raw_metadata)
            except (UnicodeDecodeError, json.JSONDecodeError):
                if len(project_id) < 80:
                    unresolved.append(entry)
                continue
            if not isinstance(metadata, dict):
                if len(project_id) < 80:
                    unresolved.append(entry)
                continue
            if metadata.get("project_id") != project_id:
                if len(project_id) < 80:
                    unresolved.append(entry)
                continue
            if metadata.get("view_id") != entry.name:
                unresolved.append(entry)
                continue
            handles.append(
                GitViewCacheHandle(
                    project_id=project_id,
                    cache_root=self._git_cache_root,
                    project_dir=project_dir,
                    view_dir=entry,
                    lock_path=project_dir / f".{entry.name}.lock",
                    metadata_digest=hashlib.sha256(raw_metadata).hexdigest(),
                )
            )
        return tuple(handles), tuple(unresolved)

    @staticmethod
    def _read_view_metadata_bytes(view_dir: Path) -> bytes | None:
        metadata_path = view_dir / "view.json"
        if metadata_path.is_symlink() or not metadata_path.is_file():
            return None
        try:
            if metadata_path.stat().st_size > 1024 * 1024:
                return None
            return metadata_path.read_bytes()
        except OSError:
            return None

    def _provider_for_base(self) -> WorkspaceProvider | None:
        provider = self._workspace_provider or get_active_workspace_provider()
        if provider is None:
            return None
        base_dir = getattr(provider, "_base_dir", None)
        if base_dir is None or absolute_path(base_dir) != self._workspace_base:
            return None
        return provider

    def _forget_project_workspaces(self, project_id: str) -> int:
        provider = self._provider_for_base()
        if provider is not None:
            return provider.forget_project_workspaces(project_id)
        return 0
