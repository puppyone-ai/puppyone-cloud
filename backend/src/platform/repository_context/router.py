from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from src.common_schemas import ApiResponse
from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import AuthorizedProject, require_project_action
from src.platform.authorization.models import ProjectAction
from src.platform.idempotency import mark_idempotency_replay, require_idempotency_key
from src.platform.repository_context.dependencies import get_repository_context_service
from src.platform.repository_context.schemas import (
    GitCredentialIssueRequest,
    GitCredentialOut,
    GitCredentialRevocationOut,
    GitRemoteOut,
    RepositoryContextResolveRequest,
    RepositoryProjectContextOut,
    RepositoryProjectSummaryOut,
)
from src.platform.repository_context.service import RepositoryContextService
from src.platform.repository_target.models import repository_target_scope_id
from src.platform.repository_target.protocol import require_repository_target_contract
from src.platform.repository_target.schemas import repository_target_schema
from src.version_engine.entrypoints.git.locator import canonical_git_url

router = APIRouter(
    tags=["repository-context"],
    dependencies=[Depends(require_repository_target_contract)],
)


def _cloud_origin(request: Request) -> str:
    return settings.PUBLIC_URL or f"{request.url.scheme}://{request.url.netloc}"


@router.post(
    "/projects/{project_id}/git-credentials",
    response_model=ApiResponse[GitCredentialOut],
    status_code=status.HTTP_201_CREATED,
)
def issue_git_credential(
    payload: GitCredentialIssueRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Depends(require_idempotency_key),
    authorized: AuthorizedProject = Depends(require_project_action(ProjectAction.CONTENT_READ)),
    current_user: CurrentUser = Depends(get_current_user),
    service: RepositoryContextService = Depends(get_repository_context_service),
):
    issued = service.issue_git_credential(
        authorized.project.id,
        current_user.user_id,
        idempotency_key,
        payload.target,
        payload.mode,
        payload.credential,
    )
    response.status_code = status.HTTP_200_OK if issued.replayed else status.HTTP_201_CREATED
    mark_idempotency_replay(response, replayed=issued.replayed)
    target = repository_target_schema(issued.target)
    return ApiResponse.success(
        data=GitCredentialOut(
            id=issued.credential_id,
            mode=issued.mode,
            remote=GitRemoteOut(
                url=canonical_git_url(
                    _cloud_origin(request),
                    issued.target.project_id,
                    repository_target_scope_id(issued.target),
                ),
                target=target,
            ),
        ),
        message="Git credential issued",
    )


@router.delete(
    "/projects/{project_id}/git-credentials/{credential_id}",
    response_model=ApiResponse[GitCredentialRevocationOut],
)
def revoke_git_credential(
    project_id: str,
    credential_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: RepositoryContextService = Depends(get_repository_context_service),
):
    service.revoke_git_credential(
        project_id,
        current_user.user_id,
        credential_id,
    )
    return ApiResponse.success(
        data=GitCredentialRevocationOut(id=credential_id),
        message="Git credential revoked",
    )


@router.post(
    "/projects/{project_id}/repository-context",
    response_model=ApiResponse[RepositoryProjectContextOut],
)
def get_repository_context(
    payload: RepositoryContextResolveRequest,
    authorized: AuthorizedProject = Depends(require_project_action(ProjectAction.PROJECT_READ)),
    current_user: CurrentUser = Depends(get_current_user),
    service: RepositoryContextService = Depends(get_repository_context_service),
):
    context = service.get_repository_context(
        authorized.project.id,
        current_user.user_id,
        payload.target,
    )
    project = context.project
    return ApiResponse.success(
        data=RepositoryProjectContextOut(
            target=repository_target_schema(context.target),
            project=RepositoryProjectSummaryOut(
                id=project.id,
                name=project.name,
                description=project.description,
                org_id=project.org_id,
                visibility=project.visibility,
                bound_git_branch=project.bound_git_branch,
                updated_at=project.updated_at.isoformat() if project.updated_at else None,
                **context.grant.as_api_fields(),
            ),
            scope_path=context.scope_path,
        )
    )
