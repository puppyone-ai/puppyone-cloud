"""
Analytics API - Access Monitoring for Context Layer

Provides time-series data for monitoring data egress (context → sandbox).
Data source: access_logs table (records each time a node is sent to sandbox).
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.infra.supabase import get_supabase_client
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

# Cap the query window so analytics cannot be used as a bulk data-export
# channel. 90 days is well beyond any dashboard use case.
_MAX_RANGE_HOURS = 24 * 90


def _require_project_access(
    authorization: AuthorizationService, project_id: str, user_id: str
) -> None:
    """Server-side tenant scoping: reject unless the caller is a member of the
    project. ``project_id`` is client-controlled input and must never be trusted
    as a scope boundary on its own.
    """
    authorization.authorize(
        project_id,
        user_id,
        ProjectAction.HISTORY_READ,
        conceal_missing_grant=False,
    )


class TimeSeriesBucket(BaseModel):
    bucket: str
    count: int


class TimeSeriesResponse(BaseModel):
    data: list[TimeSeriesBucket]
    interval: str
    range_hours: int
    total: int


@router.get("/access-timeseries", response_model=TimeSeriesResponse)
async def get_access_timeseries(
    project_id: str = Query(..., description="Project to report on (required)"),
    interval: Literal["hour", "day"] = Query("hour", description="'hour' or 'day'"),
    range_hours: int = Query(168, ge=1, le=_MAX_RANGE_HOURS, description="Hours back (default 7 days)"),
    agent_id: str | None = Query(None),
    node_name: str | None = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
):
    """
    Time-series of context access events.

    Each data point = number of times context was sent to sandbox in that time bucket.
    This is the TRUE measure of data egress.
    """
    _require_project_access(authorization, project_id, current_user.user_id)
    supabase = get_supabase_client()

    now = datetime.now(timezone.utc)
    start_time = now - timedelta(hours=range_hours)
    result = supabase.rpc(
        "analytics_access_timeseries",
        {
            "p_project_id": project_id,
            "p_start_time": start_time.isoformat(),
            "p_interval": interval,
            "p_agent_id": agent_id,
            "p_node_name": node_name,
        },
    ).execute()
    rows = result.data or []

    bucket_counts: dict[str, int] = {}
    bucket_hours = 24 if interval == "day" else 1
    for row in rows:
        raw_bucket = row.get("bucket")
        if not raw_bucket:
            continue
        dt = datetime.fromisoformat(str(raw_bucket).replace("Z", "+00:00"))
        bucket_format = "%Y-%m-%dT00:00:00Z" if interval == "day" else "%Y-%m-%dT%H:00:00Z"
        bucket_counts[dt.strftime(bucket_format)] = int(row.get("event_count") or 0)

    all_buckets: list[TimeSeriesBucket] = []
    current = start_time.replace(minute=0, second=0, microsecond=0)
    if interval == "day":
        current = current.replace(hour=0)

    bucket_format = "%Y-%m-%dT00:00:00Z" if interval == "day" else "%Y-%m-%dT%H:00:00Z"

    while current <= now:
        bucket_key = current.strftime(bucket_format)
        all_buckets.append(TimeSeriesBucket(
            bucket=bucket_key,
            count=bucket_counts.get(bucket_key, 0)
        ))
        current += timedelta(hours=bucket_hours)

    return TimeSeriesResponse(
        data=all_buckets,
        interval=interval,
        range_hours=range_hours,
        total=sum(bucket_counts.values())
    )


@router.get("/access-summary")
async def get_access_summary(
    project_id: str = Query(..., description="Project to report on (required)"),
    range_hours: int = Query(24, ge=1, le=_MAX_RANGE_HOURS),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
):
    """
    Summary stats for access monitoring.
    """
    _require_project_access(authorization, project_id, current_user.user_id)
    supabase = get_supabase_client()
    start_time = datetime.now(timezone.utc) - timedelta(hours=range_hours)
    result = supabase.rpc(
        "analytics_access_summary",
        {"p_project_id": project_id, "p_start_time": start_time.isoformat()},
    ).execute()
    row = (result.data or [{}])[0]

    return {
        "total_accesses": int(row.get("total_accesses") or 0),
        "unique_agents": int(row.get("unique_agents") or 0),
        "unique_nodes": int(row.get("unique_nodes") or 0),
        "range_hours": range_hours,
    }
