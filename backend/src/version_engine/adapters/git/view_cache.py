"""Durable per-view Git transport cache identity.

Git view caches are L6 derived protocol resources. They are consumed by the
Git smart-HTTP adapter, but PuppyOne's Version Engine remains the source of
truth for refs, history, audit, and object ownership.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.version_engine.adapters.git._filelock import try_file_exclusive_lock


ProjectionVersion = Literal["git-view-v1"]
HistoryMode = Literal["full", "receive-boundary"]
BlobMode = Literal["included", "omitted"]


@dataclass(frozen=True)
class GitViewCacheKey:
    """Composite identity for one durable Git view cache.

    Architecture doc 05-git-remote-accesspoint.md lists six components
    (``project_id + scope_path + scope_excludes + projection_version +
    history_mode + object_store_namespace``). We carry a seventh —
    ``blob_mode`` — because clone/fetch needs the cache with reachable
    blobs and receive-pack advertisement does not; sharing the same
    bare repo for both would force one direction to over-fetch. The
    extra component is a cache-efficiency optimization, not a semantic
    change to view identity; doc should be updated to match.
    """

    project_id: str
    object_store: str
    scope_path: str
    scope_excludes: tuple[str, ...]
    projection_version: ProjectionVersion
    history_mode: HistoryMode
    blob_mode: BlobMode

    @classmethod
    def from_repo(
        cls,
        repo,
        scope_path: str,
        scope_excludes: list[str] | None,
        *,
        follow_history: bool,
        include_blobs: bool,
    ) -> "GitViewCacheKey":
        return cls(
            project_id=str(getattr(repo, "_project_id", "") or "unknown-project"),
            object_store=object_store_namespace(repo),
            scope_path=scope_path or "",
            scope_excludes=tuple(sorted(scope_excludes or [])),
            projection_version="git-view-v1",
            history_mode="full" if follow_history else "receive-boundary",
            blob_mode="included" if include_blobs else "omitted",
        )

    @property
    def view_id(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.metadata_payload(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @property
    def safe_project(self) -> str:
        return "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "_"
            for ch in self.project_id
        )[:80] or "unknown-project"

    def cache_dir(self) -> Path:
        return git_view_cache_root() / self.safe_project / self.view_id

    def metadata_payload(self) -> dict:
        return {
            "project_id": self.project_id,
            "object_store": self.object_store,
            "scope_path": self.scope_path,
            "scope_excludes": list(self.scope_excludes),
            "projection_version": self.projection_version,
            "history_mode": self.history_mode,
            "blob_mode": self.blob_mode,
        }


def git_view_cache_root() -> Path:
    env = os.getenv("PUPPYONE_GIT_VIEW_CACHE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    try:
        from src.config import settings

        return Path(settings.GIT_VIEW_CACHE_DIR).expanduser()
    except Exception:
        return Path("~/.puppyone/git-view-cache").expanduser()


def write_git_view_cache_metadata(
    cache_dir: Path,
    key: GitViewCacheKey,
    *,
    head: str,
    status: str = "ready",
    view_health: str = "",
    canonical_head: str = "",
    health_reason: str = "",
    history_cut: bool = False,
) -> None:
    metadata = {
        **key.metadata_payload(),
        "view_id": key.view_id,
        "cache_head": head,
        "status": status,
    }
    if view_health:
        metadata["view_health"] = view_health
    if canonical_head:
        metadata["canonical_head"] = canonical_head
    if health_reason:
        metadata["health_reason"] = health_reason
    if history_cut:
        metadata["history_cut"] = True
    (cache_dir / "view.json").write_text(
        json.dumps(metadata, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    # Bound total cache footprint on this replica (ISSUE-011). Best-effort:
    # a pruning failure must never fail the cache write.
    try:
        prune_git_view_cache(keep=cache_dir)
    except Exception:  # noqa: BLE001
        pass


def _view_cache_dirs(root: Path) -> list[Path]:
    """All per-view cache dirs (root/<project>/<view_id>)."""
    dirs: list[Path] = []
    if not root.exists():
        return dirs
    try:
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            for view_dir in project_dir.iterdir():
                if view_dir.is_dir():
                    dirs.append(view_dir)
    except OSError:
        pass
    return dirs


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def _view_recency(view_dir: Path) -> float:
    """Recency signal = view.json mtime (refreshed on every rebuild)."""
    for candidate in (view_dir / "view.json", view_dir):
        try:
            return candidate.stat().st_mtime
        except OSError:
            continue
    return 0.0


def view_lock_path(view_dir: Path) -> Path:
    """Keep lock outside the evicted directory so Windows can remove the view."""
    return view_dir.parent / f".{view_dir.name}.lock"


def prune_git_view_cache(*, keep: Path | None = None) -> None:
    """Evict least-recently-rebuilt view caches to stay under configured caps.

    Bounds unbounded local-disk growth of the git transport view cache
    (ISSUE-011). ``keep`` (the view just written) is never evicted.
    """
    try:
        from src.config import settings

        max_bytes = int(getattr(settings, "GIT_VIEW_CACHE_MAX_BYTES", 0) or 0)
        max_views = int(getattr(settings, "GIT_VIEW_CACHE_MAX_VIEWS", 0) or 0)
    except Exception:  # noqa: BLE001
        return
    if max_bytes <= 0 and max_views <= 0:
        return

    root = git_view_cache_root().resolve()
    keep_resolved: Path | None = None
    if keep is not None:
        try:
            keep_resolved = keep.resolve()
        except OSError:
            keep_resolved = None

    entries = []  # (recency, size, dir)
    total_bytes = 0
    for view_dir in _view_cache_dirs(root):
        size = _dir_size_bytes(view_dir)
        total_bytes += size
        entries.append((_view_recency(view_dir), size, view_dir))

    entries.sort(key=lambda e: e[0])  # oldest (least recently rebuilt) first
    count = len(entries)

    for _recency, size, view_dir in entries:
        over_bytes = max_bytes > 0 and total_bytes > max_bytes
        over_count = max_views > 0 and count > max_views
        if not over_bytes and not over_count:
            break
        try:
            resolved = view_dir.resolve()
        except OSError:
            continue
        if keep_resolved is not None and resolved == keep_resolved:
            continue
        # Safety: only ever delete inside the cache root.
        if root not in {resolved, *resolved.parents}:
            continue
        with try_file_exclusive_lock(view_lock_path(view_dir)) as acquired:
            if not acquired:
                continue
            shutil.rmtree(view_dir, ignore_errors=True)
            if view_dir.exists():
                # Keep accounting accurate and continue looking for another
                # candidate instead of pretending a failed deletion freed disk.
                continue
            total_bytes -= size
            count -= 1


def invalidate_git_view_cache(key: GitViewCacheKey) -> None:
    cache_dir = key.cache_dir()
    root = git_view_cache_root().resolve()
    try:
        resolved = cache_dir.resolve()
    except FileNotFoundError:
        return
    if root not in {resolved, *resolved.parents}:
        raise ValueError(f"refusing to remove cache outside root: {cache_dir}")
    with try_file_exclusive_lock(view_lock_path(cache_dir)) as acquired:
        if not acquired:
            raise RuntimeError("git view cache is active and cannot be invalidated")
        # A rebuild is also the cache's first materialization path.  In that
        # case there is deliberately nothing to remove.  The existence check
        # must live *inside* the view lock: another rebuild may have removed
        # the directory after the path was resolved but before this caller
        # acquired the lock.  Do not use ``ignore_errors=True`` here; I/O and
        # permission failures still need to surface instead of being mistaken
        # for a successful cache invalidation.
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=False)


def object_store_namespace(repo) -> str:
    store = getattr(repo, "store", None)
    store_dir = getattr(store, "dir", None)
    backend = getattr(store, "_backend", None)
    backend_namespace = _backend_namespace(backend)
    if backend_namespace:
        return backend_namespace
    if store_dir:
        return f"store-dir:{Path(store_dir).expanduser().resolve()}"
    project_id = str(getattr(repo, "_project_id", "") or "unknown-project")
    return f"project:{project_id}"


def _backend_namespace(backend) -> str:
    if backend is None:
        return ""
    inner = getattr(backend, "_inner", None)
    if inner is not None:
        inner_namespace = _backend_namespace(inner)
        if inner_namespace:
            return f"{backend.__class__.__name__}:{inner_namespace}"
    backend_dir = getattr(backend, "dir", None)
    if backend_dir:
        return f"{backend.__class__.__name__}:{Path(backend_dir).expanduser().resolve()}"
    prefix = getattr(backend, "_prefix", "")
    s3 = getattr(backend, "_s3", None)
    if prefix:
        bucket = getattr(s3, "bucket_name", "")
        endpoint = getattr(s3, "endpoint_url", "")
        region = getattr(s3, "region", "")
        return (
            f"{backend.__class__.__name__}:"
            f"{endpoint or region}:{bucket}:{prefix}"
        )
    return ""
