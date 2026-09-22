"""FastAPI dependencies for named Project actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, Path

from src.exceptions import ErrorCode, NotFoundException
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.models import ProjectAction, ProjectGrant
from src.platform.authorization.repository import AuthorizationRepository
from src.platform.authorization.service import AuthorizationService
from src.platform.project.dependencies import get_project_repository
from src.platform.project.models import Project
from src.platform.project.repository import ProjectRepositorySupabase


@dataclass(frozen=True, slots=True)
class AuthorizedProject:
    project: Project
    grant: ProjectGrant


def get_authorization_repository() -> AuthorizationRepository:
    return AuthorizationRepository()


def get_authorization_service(
    repository: AuthorizationRepository = Depends(get_authorization_repository),
) -> AuthorizationService:
    # FastAPI caches dependencies for one request.  Keeping the service itself
    # request-scoped avoids mutable module state and makes repository overrides
    # deterministic under concurrent tests and requests.
    return AuthorizationService(repository)


def require_project_action(
    action: ProjectAction,
) -> Callable[..., AuthorizedProject]:
    def dependency(
        project_id: str = Path(..., description="Project ID"),
        current_user: CurrentUser = Depends(get_current_user),
        authorization: AuthorizationService = Depends(get_authorization_service),
        project_repository: ProjectRepositorySupabase = Depends(
            get_project_repository
        ),
    ) -> AuthorizedProject:
        grant = authorization.authorize(project_id, current_user.user_id, action)
        project = project_repository.get_by_id(project_id)
        if project is None:
            raise NotFoundException(
                f"Project not found: {project_id}", code=ErrorCode.NOT_FOUND
            )
        return AuthorizedProject(project=project, grant=grant)

    dependency.__name__ = f"require_{action.value.replace('.', '_')}"
    return dependency


require_project_read = require_project_action(ProjectAction.PROJECT_READ)
require_project_write = require_project_action(ProjectAction.PROJECT_WRITE)
require_project_admin = require_project_action(ProjectAction.PROJECT_MANAGE)
