from fastapi import Depends

from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService
from src.platform.project.dependencies import get_project_repository
from src.platform.project.repository import ProjectRepositorySupabase
from src.platform.repository_context.repository import RepositoryContextRepository
from src.platform.repository_context.service import RepositoryContextService


def get_repository_context_repository() -> RepositoryContextRepository:
    return RepositoryContextRepository()


def get_repository_context_service(
    repository: RepositoryContextRepository = Depends(get_repository_context_repository),
    authorization: AuthorizationService = Depends(get_authorization_service),
    project_repository: ProjectRepositorySupabase = Depends(get_project_repository),
) -> RepositoryContextService:
    return RepositoryContextService(repository, authorization, project_repository)
