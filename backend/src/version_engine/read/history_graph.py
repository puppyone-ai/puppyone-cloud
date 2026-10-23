"""All-ref project History application service.

This module owns the read-model boundary: one atomic ref snapshot, immutable
Git ancestry, deterministic topology, signed cursor paging, and bounded cache
reuse.  HTTP adapters only validate transport parameters and map typed values
to response schemas.
"""

from __future__ import annotations

from src.infra.execution.runner import run_owned
from src.version_engine.infrastructure.supabase.repo_manager import VersionRepoManager
from src.version_engine.read.history_cache import HistoryGraphCache
from src.version_engine.read.history_cursor import HistoryCursorCodec, history_snapshot_id
from src.version_engine.read.history_models import (
    HistoryCursorState,
    HistoryGraphCacheStats,
    HistorySnapshotUnavailableError,
    ProjectHistoryGraphPage,
)
from src.version_engine.read.native_history import NativeHistory
from src.version_engine.read.native_tree_reader import open_tree_read

_MAX_HISTORY_REFS = 512
_DEFAULT_MAX_TRAVERSAL_NODES = 200_000
_MAX_UNREADABLE_IDS_IN_RESPONSE = 20


class HistoryGraphService:
    """App-scoped service for stable, all-branch History pages."""

    def __init__(
        self,
        repo_manager: VersionRepoManager,
        *,
        cache: HistoryGraphCache | None = None,
        cursor_codec: HistoryCursorCodec | None = None,
        max_traversal_nodes: int = _DEFAULT_MAX_TRAVERSAL_NODES,
    ) -> None:
        if max_traversal_nodes <= 0:
            raise ValueError("History graph traversal limit must be positive")
        self._repos = repo_manager
        self._cache = cache or HistoryGraphCache()
        self._cursor_codec = cursor_codec or HistoryCursorCodec()
        self._max_traversal_nodes = max_traversal_nodes

    async def get_page(
        self, project_id, *, limit, cursor="", grant=None, summary=False, cancel_when=None
    ):
        return await run_owned(
            self._native_page, project_id, grant, limit, cursor, summary, cancel_when=cancel_when
        )

    def _native_page(self, project_id, grant, limit, cursor, summary=False):
        state = self._cursor_codec.decode(cursor, project_id=project_id) if cursor else None
        # Admission is fresh even on a graph-cache hit. Cached derived facts
        # never substitute for current authorization, generation or ref state.
        with self._repos.open_native_read(project_id, grant) as snapshot:
            snapshot_id = history_snapshot_id(snapshot.to_wire())
            if state and state.snapshot_id != snapshot_id:
                raise HistorySnapshotUnavailableError("repository refs changed; refresh history")
            key = (project_id, f"{snapshot.generation}:{snapshot.ref_sequence}:{snapshot_id}")

            built = []

            def build():
                snapshot.backend = snapshot.backend.pinned_reader(snapshot)
                history = NativeHistory(snapshot, max_commits=self._max_traversal_nodes)
                built.append(history)
                return history.graph()

            graph = self._cache.get_or_build(key, build)
            snapshot.check_live()
            if state and (state.roots != graph.roots or state.anchor_commit_id not in graph.nodes):
                raise HistorySnapshotUnavailableError("history cursor anchor unavailable")
            start = graph.positions[state.anchor_commit_id] + 1 if state else 0
            page_ids = graph.order[start : start + limit]
            more = start + len(page_ids) < len(graph.order)
            next_cursor = (
                self._cursor_codec.encode(
                    HistoryCursorState(
                        project_id,
                        snapshot_id,
                        graph.roots,
                        graph.head,
                        page_ids[-1],
                    )
                )
                if more and page_ids
                else None
            )
            if summary:
                entries = [self._summary(graph.nodes[oid]) for oid in page_ids]
            else:
                # Compatibility for existing clients that explicitly expect
                # changed paths in every row; summaries do not traverse trees.
                snapshot.backend = snapshot.backend.pinned_reader(snapshot)
                history = (
                    built[0]
                    if built
                    else NativeHistory(snapshot, max_commits=self._max_traversal_nodes)
                )
                entries = [history.entry(oid) for oid in page_ids]
            return ProjectHistoryGraphPage(
                entries,
                graph.refs if not cursor else (),
                not bool(cursor),
                graph.head,
                snapshot_id,
                len(graph.order),
                next_cursor,
                more,
                "complete",
                (),
                graph.root_hash,
            )

    @staticmethod
    def _summary(node):
        return {
            "commit_id": node.commit_id,
            "parent_ids": list(node.parent_ids),
            "root_hash": node.tree_id,
            "who": node.author,
            "message": node.message,
            "created_at": node.created_at,
            "changes_loaded": False,
        }

    async def get_detail(self, project_id, commit_id, *, grant, cancel_when=None):
        return await run_owned(self._detail, project_id, commit_id, grant, cancel_when=cancel_when)

    def _detail(self, project_id, commit_id, grant):
        with open_tree_read(self._repos, project_id, grant, bulk=True) as reader:
            history = NativeHistory(reader.snapshot, max_commits=self._max_traversal_nodes)
            return history.entry(commit_id)

    def cache_stats(self) -> HistoryGraphCacheStats:
        return self._cache.stats()
