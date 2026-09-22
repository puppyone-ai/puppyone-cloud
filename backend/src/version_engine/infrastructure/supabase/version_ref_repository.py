"""Version-ref store — per-scope Git branch/tag refs (GAP-3, multi-branch).

Backs the ``version_refs`` table (migration
``20260531000000_version_refs_table.sql``). A ref is a named pointer to an
already-promoted commit object; storing one here does NOT advance the
scope head (``version_scope_state``) — only a landing/merge does that.

Design: ``docs/proposals/PUP-multi-branch-design.md`` (Phase 1).

The store is deliberately small CRUD: push routing writes refs here
instead of the scope head, and the transport advertise path reads them
to expose branch/tag views.
"""

from __future__ import annotations

from src.infra.supabase.client import SupabaseClient
from src.utils.logger import log_error
from src.version_engine.infrastructure.supabase import safe_data
from src.version_engine.infrastructure.supabase.db_names import PROJECT_HISTORY_REFS_RPC
from src.version_engine.write_engine.path_utils import normalize_path


_TABLE = "version_refs"

ACCESS_POINT_MAIN_REF = "refs/heads/main"


def ref_type_for(ref_name: str) -> str | None:
    """Classify a ref name. Returns 'branch', 'tag', or None when the ref is
    not a storable named ref (e.g. the scope head ``refs/heads/main`` or an
    unrecognised namespace)."""
    if ref_name == ACCESS_POINT_MAIN_REF:
        return None
    if ref_name.startswith("refs/heads/"):
        return "branch"
    if ref_name.startswith("refs/tags/"):
        return "tag"
    return None


class VersionRefStore:
    """Thin Supabase wrapper for ``version_refs``."""

    def __init__(self, client: SupabaseClient | None = None) -> None:
        self._client = (client or SupabaseClient()).client

    def list_refs(
        self,
        project_id: str,
        scope_path: str = "",
        *,
        strict: bool = False,
    ) -> list[dict]:
        """All branch/tag refs for one (project, scope), newest update first.

        Transport advertisement keeps the historical best-effort behavior.
        Read models that promise an all-branches view pass ``strict=True`` so
        a control-plane outage cannot silently masquerade as a single branch.
        """
        try:
            resp = (
                self._client.table(_TABLE)
                .select("ref_name, ref_type, commit_id, created_by, updated_at")
                .eq("project_id", project_id)
                .eq("scope_path", normalize_path(scope_path))
                .order("updated_at", desc=True)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log_error(f"[VersionRefs] list_refs failed: {exc}")
            if strict:
                raise RuntimeError("version refs are unavailable") from exc
            return []
        return safe_data(resp) or []

    def list_project_history_refs(self, project_id: str) -> list[dict]:
        """Read canonical main plus root-scope named refs in one DB snapshot.

        History pagination must not combine a main head from one transaction
        with named refs from another.  The SQL function resolves the canonical
        project-view head against ``projects.version_root_hash`` and returns it
        together with ``version_refs`` from one PostgreSQL statement/MVCC
        snapshot.  There is deliberately no scattered-read fallback: silently
        returning a mixed snapshot would make a supposedly stable graph lie.
        """

        try:
            resp = self._client.rpc(
                PROJECT_HISTORY_REFS_RPC,
                {"p_project_id": project_id},
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log_error(f"[VersionRefs] project history ref snapshot failed: {exc}")
            raise RuntimeError("project history refs are unavailable") from exc
        return safe_data(resp) or []

    def list_all_commit_ids(self, project_id: str) -> list[str]:
        """Every commit id pointed at by a stored ref across ALL scopes.

        Used by object GC (GAP-3): a commit reachable only from a stored
        branch/tag ref must be a GC root, or its objects would be swept
        after the retention window and ``git fetch`` of that ref would
        break. Cheap — one indexed query per project.
        """
        try:
            resp = (
                self._client.table(_TABLE)
                .select("commit_id")
                .eq("project_id", project_id)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log_error(f"[VersionRefs] list_all_commit_ids failed: {exc}")
            return []
        return [
            r["commit_id"]
            for r in (safe_data(resp) or [])
            if r.get("commit_id")
        ]

    def get_ref(
        self, project_id: str, scope_path: str, ref_name: str,
    ) -> dict | None:
        try:
            resp = (
                self._client.table(_TABLE)
                .select("ref_name, ref_type, commit_id, created_by, updated_at")
                .eq("project_id", project_id)
                .eq("scope_path", normalize_path(scope_path))
                .eq("ref_name", ref_name)
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log_error(f"[VersionRefs] get_ref failed: {exc}")
            return None
        rows = safe_data(resp) or []
        return rows[0] if rows else None

    def set_ref(
        self,
        *,
        project_id: str,
        scope_path: str,
        ref_name: str,
        commit_id: str,
        created_by: str = "",
    ) -> bool:
        """Upsert a branch/tag ref to point at ``commit_id``.

        Returns ``False`` (and logs) when the ref name is not a storable
        named ref — the caller must never route ``refs/heads/main`` here,
        and an unrecognised namespace is a programming error, not a silent
        no-op that looks like success.
        """
        ref_type = ref_type_for(ref_name)
        if ref_type is None:
            log_error(
                f"[VersionRefs] refusing to store non-named ref {ref_name!r} "
                f"(project={project_id}, scope={scope_path!r})"
            )
            return False
        row = {
            "project_id": project_id,
            "scope_path": normalize_path(scope_path),
            "ref_name": ref_name,
            "ref_type": ref_type,
            "commit_id": commit_id,
            "created_by": created_by or "",
        }
        try:
            self._client.table(_TABLE).upsert(
                row, on_conflict="project_id,scope_path,ref_name",
            ).execute()
        except Exception as exc:  # noqa: BLE001
            log_error(f"[VersionRefs] set_ref failed: {exc}")
            return False
        return True

    def delete_ref(
        self, project_id: str, scope_path: str, ref_name: str,
    ) -> bool:
        try:
            resp = (
                self._client.table(_TABLE)
                .delete()
                .eq("project_id", project_id)
                .eq("scope_path", normalize_path(scope_path))
                .eq("ref_name", ref_name)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log_error(f"[VersionRefs] delete_ref failed: {exc}")
            return False
        return bool(safe_data(resp))
