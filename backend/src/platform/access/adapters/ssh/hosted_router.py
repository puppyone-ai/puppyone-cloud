"""Human credential management for SSH into the current Hosted Workspace."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.common_schemas import ApiResponse
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser

from .hosted import HostedSshAccess, build_hosted_ssh

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/workbench/ssh-grants", tags=["workspace-ssh"]
)


class SshGrantRequest(BaseModel):
    generation: UUID
    public_key: str = Field(min_length=40, max_length=8192)
    expires_in: int = Field(default=900, ge=60, le=3600)


@router.post("", response_model=ApiResponse)
async def issue(
    project_id: str,
    payload: SshGrantRequest,
    user: CurrentUser = Depends(get_current_user),
    service: HostedSshAccess = Depends(build_hosted_ssh),
):
    result = await service.issue(
        user.user_id, project_id, str(payload.generation), payload.public_key, payload.expires_in
    )
    return ApiResponse.success(data=result)


@router.delete("/{grant_id}", response_model=ApiResponse)
async def revoke(
    project_id: str,
    grant_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    service: HostedSshAccess = Depends(build_hosted_ssh),
):
    await service.revoke(user.user_id, project_id, str(grant_id))
    return ApiResponse.success(data={"revoked": True})
