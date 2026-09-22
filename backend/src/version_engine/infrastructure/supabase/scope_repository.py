"""
SupabaseScopeManager — PostgreSQL implementation of scope storage

Scope geometry lives in the dedicated `repository_scopes` table. This module
reads from there.

Scope payload:
  scope_id = repository_scopes.id
  scope    = {"id", "path", "exclude", "mode"}

The Version Engine interface keeps the generic projection key ``mode``;
storage maps it explicitly to ``repository_scopes.max_mode`` here.
"""

from __future__ import annotations

from src.infra.supabase.client import SupabaseClient
from src.repo.models import ResolvedScopeCredential
from src.utils.logger import log_error
from src.version_engine.infrastructure.supabase.scope_manager import ScopeBackend


def resolve_scope_access_credential(
    supabase: SupabaseClient,
    access_key: str,
) -> ResolvedScopeCredential | None:
    """Resolve a bearer credential, Access Surface, and exact Scope target.

    L2 identity + L1 access-point routing both need this lookup, and the
    storage work used to be duplicated between them. The repository module is
    the single source of truth so revocation, target, and capability checks
    stay together. Storage failures propagate so callers can return an
    unavailable state instead of misclassifying them as bad credentials.

    Returns ``None`` only when the credential/Surface/Scope chain is invalid.
    """
    from src.repo.scope_repository import RepositoryScopeRepository

    return RepositoryScopeRepository(supabase.client).resolve_access_key(access_key)


class SupabaseScopeBackend(ScopeBackend):
    """ScopeBackend backed by the repository_scopes table.

    Each row is the canonical scope record:
      - path / exclude / max_mode are real columns (not JSONB extraction)
      - machine credentials live hash-only in access_surface_credentials
    """

    TABLE = "repository_scopes"

    def __init__(self, supabase: SupabaseClient, project_id: str):
        self._client = supabase.client
        self._project_id = project_id

    # ── ScopeBackend interface ────────────────────────────────────────────

    def get(self, scope_id: str) -> dict | None:
        try:
            resp = (
                self._client.table(self.TABLE)
                .select("id, path, exclude, max_mode")
                .eq("id", scope_id)
                .eq("project_id", self._project_id)
                .maybe_single()
                .execute()
            )
            if not resp or not getattr(resp, "data", None):
                return None
            row = resp.data
            return {
                "id": row["id"],
                "path": row.get("path", ""),
                "exclude": row.get("exclude") or [],
                "mode": row.get("max_mode", "rw"),
            }
        except Exception as e:
            log_error(f"[ScopeBackend] get({scope_id}) failed: {e}")
            return None

    def put(self, scope_id: str, scope: dict) -> None:
        """Update an existing scope's geometry. The scope row must already
        exist (created via the repo scope_router); this is the rename /
        exclude-edit path that post-commit hooks use
        when folders move.

        Note: `path` IS updatable here even though the public scope CRUD
        API forbids it. This is the internal hook for "user renamed a
        folder, so the scope's path needs to change too" — a maintenance
        op the library performs, not a user-facing rename.
        """
        try:
            patch: dict = {}
            if "path" in scope:
                patch["path"] = scope["path"]
            if "exclude" in scope:
                patch["exclude"] = scope.get("exclude") or []
            if "mode" in scope:
                patch["max_mode"] = scope.get("mode", "rw")
            if not patch:
                return
            (
                self._client.table(self.TABLE)
                .update(patch)
                .eq("id", scope_id)
                .eq("project_id", self._project_id)
                .execute()
            )
        except Exception as e:
            log_error(f"[ScopeBackend] put({scope_id}) failed: {e}")

    def delete(self, scope_id: str) -> bool:
        """Hard-delete the scope row.

        Service layer (`scope_service.delete()`) is the user-facing path
        and refuses to delete Scopes with bound non-builtin connectors.
        This low-level method is unconditional — used by
        post-commit hooks for orphan cleanup.
        """
        try:
            resp = (
                self._client.table(self.TABLE)
                .delete()
                .eq("id", scope_id)
                .eq("project_id", self._project_id)
                .execute()
            )
            return bool(resp.data)
        except Exception as e:
            log_error(f"[ScopeBackend] delete({scope_id}) failed: {e}")
            return False

    def list_all(self) -> list[dict]:
        try:
            return self.list_all_strict()
        except Exception as e:
            log_error(f"[ScopeBackend] list_all() failed: {e}")
            return []

    def list_all_strict(self) -> list[dict]:
        """List Scope geometry while preserving storage failures.

        Authorization-time callers use this method so a database outage can
        never be mistaken for an empty descendant set.
        """

        resp = (
            self._client.table(self.TABLE)
            .select("id, path, exclude, max_mode")
            .eq("project_id", self._project_id)
            .execute()
        )
        return [
            {
                "id": row["id"],
                "path": row.get("path", ""),
                "exclude": row.get("exclude") or [],
                "mode": row.get("max_mode", "rw"),
            }
            for row in (resp.data or [])
        ]

    def find_by_path_prefix(self, path_prefix: str) -> list[dict]:
        """Find scopes whose path starts with the given prefix.

        Used by post-commit hooks to update scopes when folders are
        renamed (the canonical case: user renames /docs → /handbook,
        every scope whose path starts with 'docs/' needs its path
        updated to start with 'handbook/').
        """
        all_scopes = self.list_all()
        prefix = path_prefix.rstrip("/")
        prefix_with_slash = prefix + "/"
        return [
            s for s in all_scopes
            if s.get("path", "") == prefix
            or s.get("path", "").startswith(prefix_with_slash)
        ]
