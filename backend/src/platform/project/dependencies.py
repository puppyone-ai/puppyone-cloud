"""
Project Dependency Injection
"""

from fastapi import Depends

from src.platform.authorization.repository import ProjectMembershipRepository
from src.platform.project.repository import ProjectRepositorySupabase
from src.platform.project.service import ProjectService

# Use global variables for singletons instead of creating new instances each time
# This avoids redundant initialization and improves performance
_project_repository = None
_project_membership_repository = None
_project_service = None


def get_project_repository() -> ProjectRepositorySupabase:
    """
    Get project repository singleton

    Returns:
        ProjectRepositorySupabase instance
    """
    global _project_repository
    if _project_repository is None:
        _project_repository = ProjectRepositorySupabase()
    return _project_repository


def get_project_membership_repository() -> ProjectMembershipRepository:
    global _project_membership_repository
    if _project_membership_repository is None:
        _project_membership_repository = ProjectMembershipRepository()
    return _project_membership_repository


def get_project_service(
    repository: ProjectRepositorySupabase = Depends(get_project_repository),
    membership_repository: ProjectMembershipRepository = Depends(
        get_project_membership_repository
    ),
) -> ProjectService:
    """
    Dependency injection factory for project_service. Uses Supabase as the storage backend

    Returns:
        ProjectService singleton
    """
    global _project_service
    if (
        _project_service is None
        or _project_service.repo is not repository
        or _project_service.memberships is not membership_repository
    ):
        _project_service = ProjectService(repository, membership_repository)
    return _project_service
