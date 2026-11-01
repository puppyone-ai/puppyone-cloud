"""Human control-plane diagnostics of the canonical native repository.

Smart-HTTP routes under ``/git`` take a machine RuntimeGrant. These take the
Human ProjectGrant, so a human JWT is never forwarded to that data plane.
"""

from fastapi import APIRouter, Depends

from src.common_schemas import ApiResponse
from src.file_operations.engine_dependencies import get_repo_manager
from src.platform.access.adapters.git.native import NativeGitEndpoint
from src.platform.authorization.dependencies import AuthorizedProject, require_project_action
from src.platform.repository_target.protocol import require_repository_target_contract
from src.shared_types.grants import ProjectAction
from src.version_engine.infrastructure.supabase.repo_manager import VersionRepoManager

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses={
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
    },
)


class ProjectGitViewService:
    def __init__(self, repo_manager):
        self._repo_manager = repo_manager

    def _endpoint(self, project_id, grant):
        service = self._repo_manager.get_native_service(project_id)
        if service is None:
            raise RuntimeError("repository migration required")
        return NativeGitEndpoint(service, grant, self._repo_manager.get_audit(project_id))

    def health(self, project_id, *, grant, content_write_allowed, cache_rebuild_allowed):
        result = self._endpoint(project_id, grant).health()
        result["can_rebuild"] = cache_rebuild_allowed
        return result

    def rebuild(self, project_id, *, grant):
        # Health verifies durable objects; no local bare repository is required.
        return self._endpoint(project_id, grant).rebuild()


def get_project_git_view_service(
    repo_manager: VersionRepoManager = Depends(get_repo_manager),
) -> ProjectGitViewService:
    return ProjectGitViewService(repo_manager)


@router.get(
    "/{project_id}/git-view/health",
    response_model=ApiResponse[dict],
    summary="Get Project repository Git view health through the control plane",
)
def get_project_git_view_health(
    authorized: AuthorizedProject = Depends(require_project_action(ProjectAction.PROJECT_READ)),
    git_view: ProjectGitViewService = Depends(get_project_git_view_service),
    _repository_contract: int = Depends(require_repository_target_contract),
):
    """Return derived Git health using Human Project authorization.

    The smart-HTTP ``/git/.../health`` route intentionally remains a machine
    RuntimeGrant endpoint; a human JWT is never forwarded to that data plane.
    """

    return ApiResponse.success(
        data=git_view.health(
            str(authorized.project.id),
            grant=authorized.grant,
            content_write_allowed=authorized.grant.allows(ProjectAction.CONTENT_WRITE),
            cache_rebuild_allowed=authorized.grant.allows(ProjectAction.PROJECT_MANAGE),
        ),
        message="Project Git view health loaded",
    )


@router.post(
    "/{project_id}/git-view/rebuild-cache",
    response_model=ApiResponse[dict],
    summary="Rebuild Project repository Git view cache through the control plane",
)
def rebuild_project_git_view_cache(
    authorized: AuthorizedProject = Depends(require_project_action(ProjectAction.PROJECT_MANAGE)),
    git_view: ProjectGitViewService = Depends(get_project_git_view_service),
    _repository_contract: int = Depends(require_repository_target_contract),
):
    """Rebuild both derived root-view cache variants from canonical facts."""

    return ApiResponse.success(
        data=git_view.rebuild(str(authorized.project.id), grant=authorized.grant),
        message="Project Git view caches rebuilt from canonical facts",
    )
