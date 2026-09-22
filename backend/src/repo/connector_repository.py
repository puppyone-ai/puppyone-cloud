"""Compatibility facade over the final ``access_surfaces`` table.

Some runtime modules still import ``ConnectorRepository`` because the Python
API name predates the product vocabulary split. The storage path is no longer
the legacy ``connectors`` table; this facade delegates to Access surfaces.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from src.infra.supabase.client import SupabaseClient
from src.repo.access_surface_repository import AccessSurfaceRepository
from src.repo.models import Connector


class ConnectorRepository:
    TABLE = "access_surfaces"

    def __init__(self, supabase_client: Optional[SupabaseClient] = None):
        self._repo = AccessSurfaceRepository(supabase_client)

    # ── Reads ────────────────────────────────────────────────────────────

    def list_by_project(
        self,
        project_id: str,
        *,
        scope_id: Optional[str] = None,
        provider: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> list[Connector]:
        rows = self._repo.list_connectors_by_project(
            project_id,
            scope_id=scope_id,
            kind=provider,
        )
        if direction:
            rows = [row for row in rows if row.direction == direction]
        return rows

    def get(self, connector_id: str) -> Optional[Connector]:
        return self._repo.get_connector(connector_id)

    def get_by_scope_provider(
        self, scope_id: str, provider: str,
    ) -> Optional[Connector]:
        return self._repo.get_connector_by_scope_kind(scope_id, provider)

    def get_by_target_provider(
        self,
        project_id: str,
        scope_id: str | None,
        provider: str,
    ) -> Optional[Connector]:
        row = self._repo.get_by_target_kind(project_id, scope_id, provider)
        return self._repo.get_connector(str(row["id"])) if row else None

    def count_third_party_for_scope(self, scope_id: str) -> int:
        return self._repo.count_bound_user_surfaces(scope_id)

    # ── Writes ───────────────────────────────────────────────────────────

    def insert(
        self,
        *,
        project_id: str,
        scope_id: Optional[str],
        provider: str,
        name: str,
        direction: str,
        config: dict,
        policy: dict,
        oauth_connection_id: Optional[int],
        trigger: dict,
        created_by: Optional[str],
    ) -> Connector:
        merged_config = dict(config or {})
        merged_config["direction"] = direction
        merged_config["policy"] = policy or {}
        merged_config["trigger"] = trigger or {"type": "manual"}
        if oauth_connection_id is not None:
            merged_config["oauth_connection_id"] = oauth_connection_id
        row = self._repo.insert(
            project_id=project_id,
            scope_id=scope_id,
            kind=provider,
            name=name,
            config=merged_config,
            created_by=created_by,
        )
        return self._repo.get_connector(row["id"])

    def update(self, connector_id: str, patch: dict[str, Any]) -> Optional[Connector]:
        if not patch:
            return self.get(connector_id)
        current = self._repo.get(connector_id)
        if current is None:
            return None

        update_data: dict[str, Any] = {}
        config = dict(current.get("config") or {})
        for key, value in patch.items():
            if key == "name":
                update_data["name"] = value
                config["name"] = value
            elif key == "status":
                update_data["status"] = value
            elif key == "config":
                config.update(value or {})
            elif key == "policy":
                config["policy"] = value or {}
            elif key == "trigger":
                config["trigger"] = value or {"type": "manual"}
            elif key == "direction":
                config["direction"] = value
            elif key == "error_message":
                config["error_message"] = value
            else:
                config[key] = value

        update_data["config"] = config
        updated = self._repo.update(connector_id, update_data)
        return self._repo.get_connector(updated["id"]) if updated else None

    def update_run_status(
        self,
        connector_id: str,
        *,
        status: str,
        last_run_at: Optional[datetime] = None,
        last_run_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        self._repo.touch_run_status(
            connector_id,
            status=status,
            last_run_at=last_run_at,
            last_run_id=last_run_id,
            error_message=error_message,
        )

    def delete(self, connector_id: str) -> bool:
        return self._repo.delete(connector_id)
