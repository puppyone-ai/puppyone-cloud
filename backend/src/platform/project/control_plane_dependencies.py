from __future__ import annotations

from src.config import settings
from src.platform.project.control_plane import (
    ProjectControlPlaneRepository,
    ProjectControlPlaneService,
)

_repository: ProjectControlPlaneRepository | None = None
_service: ProjectControlPlaneService | None = None


def get_project_control_plane_repository() -> ProjectControlPlaneRepository:
    global _repository
    if _repository is None:
        _repository = ProjectControlPlaneRepository()
    return _repository


def get_project_control_plane_service() -> ProjectControlPlaneService:
    global _service
    repository = get_project_control_plane_repository()
    if _service is None or _service._repository is not repository:
        _service = ProjectControlPlaneService(
            repository,
            deletion_quiescence_seconds=settings.PROJECT_DELETION_QUIESCENCE_SECONDS,
        )
    return _service
