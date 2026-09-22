"""All-ref project History application service.

This module owns the read-model boundary: one atomic ref snapshot, immutable
Git ancestry, deterministic topology, signed cursor paging, and bounded cache
reuse.  HTTP adapters only validate transport parameters and map typed values
to response schemas.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import time

from src.utils.logger import log_error, log_info
from src.version_engine.infrastructure.supabase.repo_manager import VersionRepoManager
from src.version_engine.infrastructure.supabase.version_ref_repository import VersionRefStore
from src.version_engine.read.history_cache import HistoryGraphCache
from src.version_engine.read.history_cursor import HistoryCursorCodec
from src.version_engine.read.history_facts import read_graph_commit
from src.version_engine.read.history_models import (
    GraphCommit,
    HistoryCursorError,
    HistoryCursorState,
    HistoryGraphCacheStats,
    HistoryGraphSnapshot,
    HistoryGraphTooLargeError,
    HistoryRef,
    HistoryRefsUnavailableError,
    HistorySnapshotUnavailableError,
    ProjectHistoryGraphPage,
)
from src.version_engine.write_engine.git_commit import is_git_object_id
from src.version_engine.write_engine.git_object_format import decode_tag


_MAX_HISTORY_REFS = 512
_DEFAULT_MAX_TRAVERSAL_NODES = 200_000
_MAX_UNREADABLE_IDS_IN_RESPONSE = 20


class HistoryGraphService:
    """App-scoped service for stable, all-branch History pages."""

    def __init__(
        self,
        repo_manager: VersionRepoManager,
        version_refs: VersionRefStore,
        *,
        cache: HistoryGraphCache | None = None,
        cursor_codec: HistoryCursorCodec | None = None,
        max_traversal_nodes: int = _DEFAULT_MAX_TRAVERSAL_NODES,
    ) -> None:
        if max_traversal_nodes <= 0:
            raise ValueError("History graph traversal limit must be positive")
        self._repos = repo_manager
        self._version_refs = version_refs
        self._cache = cache or HistoryGraphCache()
        self._cursor_codec = cursor_codec or HistoryCursorCodec()
        self._max_traversal_nodes = max_traversal_nodes

    async def get_page(
        self,
        project_id: str,
        *,
        limit: int,
        cursor: str = "",
    ) -> ProjectHistoryGraphPage:
        if cursor:
            cursor_state = self._cursor_codec.decode(cursor, project_id=project_id)
            refs: tuple[HistoryRef, ...] = ()
            refs_included = False
        else:
            cursor_state = None
            refs = ()
            refs_included = True

        repo = self._repos.get_repo(project_id)
        if cursor_state is None:
            refs = await self._load_ref_snapshot(project_id, repo)
            roots = tuple(dict.fromkeys(ref.commit_id for ref in refs))
            cursor_state = HistoryCursorState(
                project_id=project_id,
                snapshot_id=_history_snapshot_id(refs),
                roots=roots,
                head_commit_id=next(
                    (ref.commit_id for ref in refs if ref.ref_name == "refs/heads/main"),
                    "",
                ),
                anchor_commit_id="",
            )

        if not cursor_state.roots:
            return ProjectHistoryGraphPage(
                entries=[],
                refs=refs,
                refs_included=refs_included,
                head_commit_id=cursor_state.head_commit_id,
                snapshot_id=cursor_state.snapshot_id,
                total=0,
                next_cursor=None,
                has_more=False,
                graph_health="complete",
                unreadable_commit_ids=(),
            )

        cache_key = (project_id, cursor_state.snapshot_id)
        snapshot = await asyncio.to_thread(
            self._cache.get_or_build,
            cache_key,
            lambda: self._build_snapshot(project_id, repo, cursor_state.roots),
        )

        start = 0
        if cursor_state.anchor_commit_id:
            anchor_position = snapshot.positions.get(cursor_state.anchor_commit_id)
            if anchor_position is None:
                raise HistorySnapshotUnavailableError(
                    "history cursor anchor is unavailable; refresh the history snapshot"
                )
            start = anchor_position + 1

        page_ids = list(snapshot.order[start:start + max(1, limit)])
        end = start + len(page_ids)
        has_more = end < len(snapshot.order)
        metadata = await asyncio.to_thread(_load_history_metadata, repo.history, page_ids)
        entries = [
            _graph_commit_to_history_entry(snapshot.nodes[commit_id], metadata.get(commit_id))
            for commit_id in page_ids
        ]
        next_cursor = None
        if has_more and page_ids:
            next_cursor = self._cursor_codec.encode(HistoryCursorState(
                project_id=project_id,
                snapshot_id=cursor_state.snapshot_id,
                roots=cursor_state.roots,
                head_commit_id=cursor_state.head_commit_id,
                anchor_commit_id=page_ids[-1],
            ))

        unreadable = snapshot.unreadable_commit_ids[:_MAX_UNREADABLE_IDS_IN_RESPONSE]
        return ProjectHistoryGraphPage(
            entries=entries,
            refs=refs,
            refs_included=refs_included,
            head_commit_id=cursor_state.head_commit_id,
            snapshot_id=cursor_state.snapshot_id,
            total=len(snapshot.order),
            next_cursor=next_cursor,
            has_more=has_more,
            graph_health="degraded" if snapshot.unreadable_commit_ids else "complete",
            unreadable_commit_ids=unreadable,
        )

    def cache_stats(self) -> HistoryGraphCacheStats:
        return self._cache.stats()

    def _build_snapshot(
        self,
        project_id: str,
        repo,
        roots: tuple[str, ...],
    ) -> HistoryGraphSnapshot:
        started_at = time.monotonic()
        snapshot = _build_graph_snapshot(
            repo,
            roots,
            max_nodes=self._max_traversal_nodes,
        )
        elapsed_ms = (time.monotonic() - started_at) * 1000
        log_info(
            "[history-graph] snapshot built "
            f"project={project_id} roots={len(roots)} commits={len(snapshot.order)} "
            f"unreadable={len(snapshot.unreadable_commit_ids)} elapsed_ms={elapsed_ms:.1f}"
        )
        return snapshot

    async def _load_ref_snapshot(self, project_id: str, repo) -> tuple[HistoryRef, ...]:
        try:
            rows = await asyncio.to_thread(
                self._version_refs.list_project_history_refs,
                project_id,
            )
        except RuntimeError as exc:
            raise HistoryRefsUnavailableError("History refs are unavailable") from exc
        return await asyncio.to_thread(_normalize_history_refs, rows, repo)


def _normalize_history_refs(rows: list[dict], repo) -> tuple[HistoryRef, ...]:
    refs_by_name: dict[str, HistoryRef] = {}
    for row in rows:
        ref_name = str(row.get("ref_name") or "")
        ref_type = str(row.get("ref_type") or "")
        commit_id = str(row.get("commit_id") or "")
        expected_prefix = "refs/heads/" if ref_type == "branch" else "refs/tags/"
        if (
            ref_name in refs_by_name
            or ref_type not in {"branch", "tag"}
            or not ref_name.startswith(expected_prefix)
            or ref_name == expected_prefix
            or not is_git_object_id(commit_id)
        ):
            raise HistoryRefsUnavailableError("History ref snapshot is invalid")
        refs_by_name[ref_name] = HistoryRef(
            ref_name=ref_name,
            ref_type=ref_type,
            commit_id=commit_id,
        )
    refs = tuple(sorted(
        refs_by_name.values(),
        key=lambda ref: (
            0 if ref.ref_name == "refs/heads/main" else 1,
            0 if ref.ref_type == "branch" else 1,
            ref.ref_name,
        ),
    ))
    if len(refs) > _MAX_HISTORY_REFS:
        raise HistoryGraphTooLargeError(
            f"history ref snapshot exceeds the {_MAX_HISTORY_REFS} ref safety limit"
        )
    return tuple(
        HistoryRef(
            ref_name=ref.ref_name,
            ref_type=ref.ref_type,
            commit_id=(
                _peel_annotated_tag_to_commit(repo, ref.commit_id)
                if ref.ref_type == "tag"
                else ref.commit_id
            ),
        )
        for ref in refs
    )


def _peel_annotated_tag_to_commit(repo, object_id: str) -> str:
    """Return the commit target for lightweight, annotated, or nested tags.

    The persistent ref continues to point at its original Git object. History
    exposes a commit graph, so its read-model ref is intentionally peeled just
    like ``git log --all``. Invalid/unavailable tags fall back to the raw id;
    normal traversal then reports that root through graph health.
    """

    current = object_id
    expected_type: str | None = None
    visited: set[str] = set()
    for _depth in range(16):
        if current in visited:
            return object_id
        visited.add(current)
        try:
            obj_type, content = repo.store.get_object(current)
        except Exception:  # noqa: BLE001 - traversal reports the raw root as degraded
            return object_id
        if expected_type and obj_type != expected_type:
            return object_id
        if obj_type == "commit":
            return current
        if obj_type != "tag":
            return object_id
        try:
            tag = decode_tag(content)
        except (UnicodeDecodeError, ValueError):
            return object_id
        expected_type = str(tag.get("type") or "")
        if expected_type not in {"commit", "tag"}:
            return object_id
        current = str(tag.get("object") or "")
    return object_id


def _history_snapshot_id(refs: tuple[HistoryRef, ...]) -> str:
    payload = json.dumps(
        [[ref.ref_name, ref.ref_type, ref.commit_id] for ref in refs],
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _build_graph_snapshot(repo, roots: tuple[str, ...], *, max_nodes: int) -> HistoryGraphSnapshot:
    nodes: dict[str, GraphCommit] = {}
    root_ranks: dict[str, int] = {}
    unreadable: set[str] = set()
    stack = [(commit_id, rank) for rank, commit_id in reversed(list(enumerate(roots)))]

    while stack:
        commit_id, rank = stack.pop()
        previous_rank = root_ranks.get(commit_id)
        if previous_rank is not None and previous_rank <= rank:
            continue
        root_ranks[commit_id] = rank

        node = nodes.get(commit_id)
        if node is None:
            if len(nodes) >= max_nodes:
                raise HistoryGraphTooLargeError(
                    f"history graph exceeds the {max_nodes} commit traversal safety limit"
                )
            node = read_graph_commit(repo, commit_id)
            if node is None:
                unreadable.add(commit_id)
                continue
            nodes[commit_id] = node
        for parent_id in reversed(node.parent_ids):
            stack.append((parent_id, rank))

    order = tuple(_topological_commit_order(nodes, root_ranks))
    return HistoryGraphSnapshot(
        order=order,
        positions={commit_id: index for index, commit_id in enumerate(order)},
        nodes=nodes,
        unreadable_commit_ids=tuple(sorted(unreadable)),
    )


def _topological_commit_order(
    nodes: dict[str, GraphCommit],
    root_ranks: dict[str, int],
) -> list[str]:
    pending_children = dict.fromkeys(nodes, 0)
    for node in nodes.values():
        for parent_id in node.parent_ids:
            if parent_id in pending_children:
                pending_children[parent_id] += 1

    ready: list[tuple[int, int, str]] = []
    for commit_id, child_count in pending_children.items():
        if child_count == 0:
            node = nodes[commit_id]
            heapq.heappush(
                ready,
                (root_ranks.get(commit_id, len(root_ranks)), -node.timestamp, commit_id),
            )

    order: list[str] = []
    while ready:
        _rank, _timestamp, commit_id = heapq.heappop(ready)
        order.append(commit_id)
        for parent_id in nodes[commit_id].parent_ids:
            if parent_id not in pending_children:
                continue
            pending_children[parent_id] -= 1
            if pending_children[parent_id] == 0:
                parent = nodes[parent_id]
                heapq.heappush(
                    ready,
                    (
                        root_ranks.get(parent_id, len(root_ranks)),
                        -parent.timestamp,
                        parent_id,
                    ),
                )

    if len(order) != len(nodes):
        remaining = sorted(set(nodes) - set(order), key=lambda commit_id: (
            root_ranks.get(commit_id, len(root_ranks)),
            -nodes[commit_id].timestamp,
            commit_id,
        ))
        order.extend(remaining)
    return order


def _load_history_metadata(history, commit_ids: list[str]) -> dict[str, dict]:
    try:
        batch_getter = getattr(history, "get_entries", None)
        if callable(batch_getter):
            rows = batch_getter(commit_ids)
        else:
            rows = [history.get_entry(commit_id) for commit_id in commit_ids]
    except Exception as exc:  # noqa: BLE001 - immutable Git facts remain valid
        log_error(f"[history-graph] history metadata lookup failed: {exc}")
        rows = []
    return {
        row.get("commit_id", ""): row
        for row in rows
        if row and row.get("commit_id")
    }


def _graph_commit_to_history_entry(node: GraphCommit, metadata: dict | None) -> dict:
    metadata = metadata or {}
    return {
        "commit_id": node.commit_id,
        "parent_ids": list(node.parent_ids),
        "who": metadata.get("who") or node.author,
        "message": metadata.get("message") or node.message,
        "changes": metadata.get("changes") or [],
        "conflicts": metadata.get("conflicts") or [],
        "root_hash": metadata.get("root_hash") or node.tree_id,
        "scope_hash": metadata.get("scope_hash") or node.tree_id,
        "scope_path": metadata.get("scope_path") or "",
        "created_at": metadata.get("created_at") or node.created_at,
        "audit_detail": metadata.get("audit_detail"),
    }
