"""Database facts for the canonical Project policy decision point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProjectAuthorizationFacts:
    project_id: str
    org_id: str
    visibility: str
    org_role: str | None
    project_role: str | None
    project_member_org_id: str | None


class AuthorizationRepository:
    """Read-only authorization facts.

    This repository is the only human authorization module allowed to read
    `org_members` and `project_members`. It returns raw facts; policy remains in
    `AuthorizationService` so SQL access and product decisions stay separate.
    """

    def __init__(self, supabase_client: Any | None = None):
        if supabase_client is None:
            from src.infra.supabase.dependencies import get_supabase_client

            supabase_client = get_supabase_client()
        self._client = supabase_client

    @staticmethod
    def _first(response: Any) -> dict[str, Any] | None:
        data = getattr(response, "data", None)
        if isinstance(data, dict):
            return data
        return data[0] if data else None

    def load_project_facts(self, project_id: str, user_id: str) -> ProjectAuthorizationFacts | None:
        project = self._first(
            self._client.table("projects")
            .select("id, org_id, visibility")
            .eq("id", project_id)
            .eq("lifecycle_status", "ready")
            .limit(1)
            .execute()
        )
        if not project:
            return None

        org_id = str(project["org_id"])
        org_member = self._first(
            self._client.table("org_members")
            .select("role")
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        project_member = self._first(
            self._client.table("project_members")
            .select("role, org_id")
            .eq("project_id", project_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        return ProjectAuthorizationFacts(
            project_id=str(project["id"]),
            org_id=org_id,
            visibility=str(project.get("visibility") or "private"),
            org_role=str(org_member["role"]) if org_member else None,
            project_role=(str(project_member["role"]) if project_member else None),
            project_member_org_id=(
                str(project_member["org_id"])
                if project_member and project_member.get("org_id") is not None
                else None
            ),
        )

    def load_project_facts_batch(
        self, project_ids: list[str], user_id: str
    ) -> dict[str, ProjectAuthorizationFacts]:
        """Load list-page authorization facts without an N+1 query pattern."""
        normalized_ids = list(dict.fromkeys(str(value) for value in project_ids if value))
        if not normalized_ids:
            return {}

        projects = (
            self._client.table("projects")
            .select("id, org_id, visibility")
            .in_("id", normalized_ids)
            .eq("lifecycle_status", "ready")
            .execute()
        ).data or []
        org_ids = list({str(row["org_id"]) for row in projects})
        org_members = []
        if org_ids:
            org_members = (
                self._client.table("org_members")
                .select("org_id, role")
                .eq("user_id", user_id)
                .in_("org_id", org_ids)
                .execute()
            ).data or []
        project_members = (
            self._client.table("project_members")
            .select("project_id, org_id, role")
            .eq("user_id", user_id)
            .in_("project_id", normalized_ids)
            .execute()
        ).data or []

        org_role_by_id = {str(row["org_id"]): str(row["role"]) for row in org_members}
        project_member_by_id = {str(row["project_id"]): row for row in project_members}
        result: dict[str, ProjectAuthorizationFacts] = {}
        for project in projects:
            project_id = str(project["id"])
            org_id = str(project["org_id"])
            member = project_member_by_id.get(project_id)
            result[project_id] = ProjectAuthorizationFacts(
                project_id=project_id,
                org_id=org_id,
                visibility=str(project.get("visibility") or "private"),
                org_role=org_role_by_id.get(org_id),
                project_role=str(member["role"]) if member else None,
                project_member_org_id=(
                    str(member["org_id"]) if member and member.get("org_id") is not None else None
                ),
            )
        return result


class ProjectMembershipRepository:
    """Administration port for the canonical Human membership fact table.

    Policy resolution remains in :class:`AuthorizationService`; Project
    settings code uses this port so raw membership storage never leaks into
    unrelated business services.
    """

    def __init__(self, supabase_client: Any | None = None):
        if supabase_client is None:
            from src.infra.supabase.dependencies import get_supabase_client

            supabase_client = get_supabase_client()
        self._client = supabase_client

    def list_by_project(self, project_id: str) -> list[dict[str, Any]]:
        try:
            response = (
                self._client.table("project_members")
                .select("*, profiles(email, display_name, avatar_url)")
                .eq("project_id", project_id)
                .order("created_at")
                .execute()
            )
        except Exception:
            response = (
                self._client.table("project_members")
                .select("*")
                .eq("project_id", project_id)
                .order("created_at")
                .execute()
            )
        return response.data or []

    def get(self, project_id: str, target_user_id: str) -> dict[str, Any] | None:
        response = (
            self._client.table("project_members")
            .select("id, org_id, project_id, user_id, role")
            .eq("project_id", project_id)
            .eq("user_id", target_user_id)
            .limit(1)
            .execute()
        )
        return self._row(response.data)

    def is_billable_organization_member(self, org_id: str, user_id: str) -> bool:
        """Use the database-owned capability policy for seat transitions."""

        data = (
            self._client.rpc(
                "is_billable_organization_member",
                {"p_org_id": org_id, "p_user_id": user_id},
            )
            .execute()
            .data
        )
        if isinstance(data, list):
            data = data[0] if data else False
        return bool(data)

    @staticmethod
    def _row(data: Any) -> dict[str, Any] | None:
        rows = data or []
        if isinstance(rows, list):
            return rows[0] if rows else None
        return rows if isinstance(rows, dict) else None

    def add(
        self,
        project_id: str,
        target_user_id: str,
        role: str,
        actor_user_id: str,
    ) -> dict[str, Any] | None:
        return self._row(
            self._client.rpc(
                "add_project_member_authorized",
                {
                    "p_project_id": project_id,
                    "p_target_user_id": target_user_id,
                    "p_role": role,
                    "p_actor_user_id": actor_user_id,
                },
            )
            .execute()
            .data
        )

    def update_role(
        self,
        project_id: str,
        target_user_id: str,
        role: str,
        actor_user_id: str,
    ) -> dict[str, Any] | None:
        return self._row(
            self._client.rpc(
                "update_project_member_role_authorized",
                {
                    "p_project_id": project_id,
                    "p_target_user_id": target_user_id,
                    "p_role": role,
                    "p_actor_user_id": actor_user_id,
                },
            )
            .execute()
            .data
        )

    def remove(self, project_id: str, target_user_id: str, actor_user_id: str) -> bool:
        data = (
            self._client.rpc(
                "remove_project_member_authorized",
                {
                    "p_project_id": project_id,
                    "p_target_user_id": target_user_id,
                    "p_actor_user_id": actor_user_id,
                },
            )
            .execute()
            .data
        )
        if isinstance(data, list):
            return bool(data and data[0])
        return bool(data)

    def join_with_share_token(self, share_token: str, user_id: str) -> dict[str, Any] | None:
        return self._row(
            self._client.rpc(
                "join_project_via_share_token",
                {"p_share_token": share_token, "p_user_id": user_id},
            )
            .execute()
            .data
        )
