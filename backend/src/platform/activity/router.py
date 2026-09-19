"""Routes for the unified context-activity feed (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.common_schemas import ApiResponse
from src.platform.activity.dependencies import get_activity_service
from src.platform.activity.schemas import ActivityListResponse
from src.platform.activity.service import ActivityService
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=ApiResponse[ActivityListResponse])
def list_activity(
    project_id: str = Query(..., description="Project ID"),
    kind: str | None = Query(
        None, description="Filter by kind: upload | import | sync_run"
    ),
    active_only: bool = Query(False, description="Only in-progress items"),
    limit: int = Query(50, ge=1, le=200),
    service: ActivityService = Depends(get_activity_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Unified upload / import / sync_run activity for a project.

    Read-only aggregation over the ``context_activity_items`` view. Each item
    keeps its own ``kind`` and lifecycle; this endpoint never writes. The
    service uses synchronous PostgREST, so FastAPI runs this complete read
    (including authorization) in its bounded AnyIO worker pool.
    """
    items = service.list_for_project(
        project_id,
        current_user.user_id,
        kind=kind,
        active_only=active_only,
        limit=limit,
    )
    return ApiResponse.success(
        ActivityListResponse(items=items, total=len(items)),
    )
