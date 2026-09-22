"""Persistence boundary for derived Project readiness facts."""

from __future__ import annotations

from typing import Any

from src.version_engine.infrastructure.supabase.db_names import SCOPE_STATE_TABLE


class ProjectReadinessRepository:
    def __init__(self, supabase_client: Any | None = None):
        if supabase_client is None:
            from src.infra.supabase.dependencies import get_supabase_client

            supabase_client = get_supabase_client()
        self._client = supabase_client

    def load(self, project_id: str) -> dict[str, Any]:
        project_rows = (
            self._client.table("projects")
            .select("bound_git_branch")
            .eq("id", project_id)
            .limit(1)
            .execute()
        ).data or []
        surface_rows = (
            self._client.table("access_surfaces")
            .select("id")
            .eq("project_id", project_id)
            .is_("scope_id", "null")
            .eq("kind", "git_remote")
            .eq("status", "active")
            .limit(1)
            .execute()
        ).data or []
        state_rows = (
            self._client.table(SCOPE_STATE_TABLE)
            .select("head_commit_id")
            .eq("project_id", project_id)
            .eq("scope_path", "")
            .limit(1)
            .execute()
        ).data or []
        # A Product/API write can create a canonical root head, but it must not
        # silently satisfy the explicit "create Git + first root push" product
        # gate.  version_transactions is the durable acceptance ledger and is
        # written in the same publish transaction as the accepted Git ref.
        accepted_root_pushes = (
            self._client.table("version_transactions")
            .select("id")
            .eq("project_id", project_id)
            .eq("scope_path", "")
            .eq("source_channel", "access_git")
            .eq("status", "committed")
            .limit(1)
            .execute()
        ).data or []
        return {
            "default_branch": (
                str(project_rows[0].get("bound_git_branch") or "main")
                if project_rows
                else "main"
            ),
            "project_git_surface_exists": bool(surface_rows),
            "project_head_commit_id": (
                str(state_rows[0].get("head_commit_id") or "")
                if state_rows
                else ""
            ),
            "project_git_push_accepted": bool(accepted_root_pushes),
        }
