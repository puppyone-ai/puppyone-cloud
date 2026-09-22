"""Service for the unified context-activity feed."""

from __future__ import annotations

from fastapi import HTTPException, status

from src.platform.activity.repository import ACTIVITY_KINDS, ActivityRepository
from src.platform.activity.schemas import ActivityItemResponse
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService


class ActivityService:
    def __init__(
        self, *, repo: ActivityRepository, authorization: AuthorizationService
    ):
        self.repo = repo
        self.authorization = authorization

    def list_for_project(
        self,
        project_id: str,
        user_id: str,
        *,
        kind: str | None = None,
        active_only: bool = False,
        limit: int = 50,
    ) -> list[ActivityItemResponse]:
        # Authorization boundary: the view is read with the service-role
        # client, so we must verify the caller can access this project before
        # returning any rows. Mirrors ImportJobService.list_for_project.
        self.authorization.authorize(project_id, user_id, ProjectAction.HISTORY_READ)

        if kind is not None and kind not in ACTIVITY_KINDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"kind must be one of {ACTIVITY_KINDS}",
            )

        return self.repo.list_by_project(
            project_id,
            kind=kind,
            active_only=active_only,
            limit=limit,
        )
