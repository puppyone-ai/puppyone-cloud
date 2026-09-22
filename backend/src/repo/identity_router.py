"""Repo identity endpoint — the single "access point" page surface.

Returns the project's Git/CLI access surface + prompt template + scope metadata. This is
what the new frontend /access page renders.

Path: /api/v1/projects/{project_id}/access-point
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from src.common_schemas import ApiResponse
from src.config import settings
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter
from src.version_engine.adapters.product.operation_adapter import ProductOperationAdapter
from src.platform.authorization.dependencies import AuthorizedProject, require_project_action
from src.platform.authorization.models import ProjectAction
from src.platform.repository_target.protocol import require_repository_target_contract
from src.repo.scope_service import ScopeService
from src.repo.scope_router import get_scope_service
from src.repo.schemas import (
    RepoIdentityOut, RepoIdentityScopeOut, RepoIdentityPatch,
)
from src.version_engine.entrypoints.git.locator import canonical_git_url


router = APIRouter(
    prefix="/projects/{project_id}/access-point",
    tags=["repo-identity"],
    dependencies=[Depends(require_repository_target_contract)],
)


def _build_repo_url(project_id: str, request: Request) -> str:
    """Compute the project's Git remote URL.

    V1 post-hash-removal: the project-level Git smart-HTTP surface
    lives at ``/git/{project_id}.git``; the legacy ``/api/v1/version/{project_id}``
    endpoint was deleted with the wire protocol. Prefer
    ``settings.PUBLIC_URL`` when set (production); fall back to the
    request's own host header so dev / staging show the right thing
    without extra config.
    """
    base = settings.PUBLIC_URL
    if not base:
        # Best-effort fallback — request.url.scheme/netloc.
        base = f"{request.url.scheme}://{request.url.netloc}"
    return canonical_git_url(base, project_id)


@router.get(
    "",
    response_model=ApiResponse[RepoIdentityOut],
    summary="Get the project's access point (URL + prompt + scope keys)",
)
def get_access_point(
    request: Request,
    authorized: AuthorizedProject = Depends(
        require_project_action(ProjectAction.ACCESS_READ)
    ),
    scope_service: ScopeService = Depends(get_scope_service),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    project = authorized.project
    scopes = scope_service.list_for_project(str(project.id))

    head_commit_id = ops.get_head_commit_id(str(project.id)) or ""

    return ApiResponse.success(
        data=RepoIdentityOut(
            project_id=str(project.id),
            url=_build_repo_url(str(project.id), request),
            prompt_template=getattr(project, "prompt_template", "") or "",
            scopes=[
                RepoIdentityScopeOut(
                    id=s.id,
                    name=s.name,
                    path=s.path,
                    git_url=canonical_git_url(
                        (
                            settings.PUBLIC_URL
                            or f"{request.url.scheme}://{request.url.netloc}"
                        ),
                        str(project.id),
                        s.id,
                    ),
                )
                for s in scopes
            ],
            content_initialized=bool(head_commit_id),
            head_commit_id=head_commit_id or None,
        ),
        message="Access point retrieved",
    )


@router.patch(
    "",
    response_model=ApiResponse[None],
    summary="Update the project's prompt template",
)
def update_access_point(
    payload: RepoIdentityPatch,
    authorized: AuthorizedProject = Depends(
        require_project_action(ProjectAction.PROJECT_MANAGE)
    ),
):
    project = authorized.project
    if payload.prompt_template is not None:
        # Reuse the project service if it has an update method; otherwise
        # write directly via Supabase client. (Keeping this loose so the
        # project service can grow this method later without breaking us.)
        from src.infra.supabase.client import SupabaseClient
        SupabaseClient().get_client().table("projects").update({
            "prompt_template": payload.prompt_template,
        }).eq("id", str(project.id)).execute()
    return ApiResponse.success(message="Access point updated")
