"""FastAPI dependencies for the context-activity feed."""

from __future__ import annotations

from fastapi import Depends

from src.platform.activity.repository import ActivityRepository
from src.platform.activity.service import ActivityService
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService

_activity_repo: ActivityRepository | None = None


def get_activity_repository() -> ActivityRepository:
    global _activity_repo
    if _activity_repo is None:
        _activity_repo = ActivityRepository()
    return _activity_repo


def get_activity_service(
    authorization: AuthorizationService = Depends(get_authorization_service),
) -> ActivityService:
    return ActivityService(
        repo=get_activity_repository(),
        authorization=authorization,
    )
