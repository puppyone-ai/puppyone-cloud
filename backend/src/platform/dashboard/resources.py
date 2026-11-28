"""Dashboard resources, with domain-qualified identities and usage.

The legacy dashboard remains a bounded installed-client contract. This boundary
never joins Access and Synchronize by ID/path, and never turns a failed read into
an apparently empty inventory. Physical relation cutover belongs to ISSUE-049.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import HTTPException

from src.platform.access.config_redaction import redact_config
from src.platform.access.surface_repository import AccessSurfaceRepository
from src.platform.dashboard.schemas import (
    DashboardAccessSurface,
    DashboardResource,
    DashboardSynchronizeBinding,
)
from src.platform.synchronize.classification import ensure_classified_binding
from src.platform.synchronize.repository import SynchronizeRepository
from src.platform.synchronize.run_repository import NEW_TABLE as SYNCHRONIZE_RUNS_TABLE


def _usage(
    sb,
    table: str,
    key: str,
    ids: list[str],
    *,
    project_id: str | None = None,
    started: str = "started_at",
    days: int = 14,
):
    buckets = {identity: [0] * days for identity in ids}
    if not ids:
        return buckets
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)
    query = (
        sb.table(table)
        .select(f"{key}, {started}")
        .in_(key, ids)
        .gte(started, start.isoformat())
    )
    if project_id is not None:
        query = query.eq("project_id", project_id)
    for row in query.execute().data or []:
        identity = row.get(key)
        try:
            timestamp = datetime.fromisoformat(row[started].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        index = (timestamp.astimezone(UTC).date() - start).days
        if identity in buckets and 0 <= index < days:
            buckets[identity][index] += 1
    return buckets


def fetch_dashboard_resources(sb, project_id: str) -> list[DashboardResource]:
    bindings = SynchronizeRepository(SimpleNamespace(client=sb)).list_by_project(project_id)
    for binding in bindings:
        ensure_classified_binding(binding)
    access_repo = AccessSurfaceRepository(sb)
    surfaces = [
        row
        for row in access_repo.list_by_project(project_id)
        if ((row.get("config") or {}).get("trigger") or {}).get("type") != "import_once"
    ]
    scopes = access_repo.scope_rows_for(surfaces)
    # Distinct ID sets and log stores: equal IDs across domains cannot share runs.
    synchronize_usage = _usage(
        sb,
        SYNCHRONIZE_RUNS_TABLE,
        "synchronize_binding_id",
        [row.id for row in bindings],
        project_id=project_id,
    )
    # Agent usage counts durable Cloud Agent runs by the day they were submitted.
    agent_usage = _usage(
        sb,
        "agent_runs",
        "agent_id",
        [row["id"] for row in surfaces if row["kind"] == "agent"],
        project_id=project_id,
        started="created_at",
    )
    result: list[DashboardResource] = []
    for binding in bindings:
        if binding.project_id != project_id:
            raise HTTPException(409, "Dashboard returned a foreign Synchronize identity")
        result.append(
            DashboardSynchronizeBinding(
                resource_id=binding.id,
                project_id=project_id,
                provider=binding.provider,
                name=binding.config.get("name")
                or binding.config.get("source", {}).get("resource_name")
                or binding.provider,
                path=binding.path,
                direction=binding.direction,
                status=binding.status,
                trigger=redact_config(binding.trigger),
                last_activity_at=binding.last_synced_at,
                error_message=binding.error_message,
                created_at=binding.created_at,
                usage_buckets=synchronize_usage[binding.id],
            )
        )
    for row in surfaces:
        if row["project_id"] != project_id:
            raise HTTPException(409, "Dashboard returned a foreign Access identity")
        scope_id = row.get("scope_id")
        scope = scopes.get(scope_id) if scope_id else None
        if scope_id and (
            not scope or scope.get("project_id") != project_id or not scope.get("path")
        ):
            raise HTTPException(409, "Access target requires repository Scope repair")
        target = (
            {"kind": "scope", "project_id": project_id, "scope_id": scope_id}
            if scope_id
            else {"kind": "project_root", "project_id": project_id}
        )
        config = row.get("config") or {}
        result.append(
            DashboardAccessSurface(
                resource_id=row["id"],
                project_id=project_id,
                kind=row["kind"],
                target=target,
                name=row.get("name"),
                path=scope["path"] if scope else "",
                status=row.get("status") or "active",
                direction=config.get("direction"),
                trigger=redact_config(config.get("trigger")),
                last_activity_at=config.get("last_seen_at") or config.get("last_run_at"),
                error_message=config.get("error_message"),
                created_at=row.get("created_at"),
                scope_mode=scope.get("max_mode") if scope else None,
                usage_buckets=agent_usage.get(row["id"], [0] * 14)
                if row["kind"] == "agent"
                else [0] * 14,
            )
        )
    return result
