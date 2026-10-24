"""
Workspace API — Folder interface for external Agents

Endpoints:
  POST /workspace/create                Create workspace (returns path)
  POST /workspace/{agent_id}/complete   Trigger merge after Agent completes (via Version Engine)
  GET  /workspace/{agent_id}/status     View workspace status
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.common_schemas import ApiResponse
from src.file_operations.commands import VersionWriteCommandService
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService
from src.platform.billing.runtime import guard_unmetered_hosted_runtime
from src.platform.entitlements.dependencies import get_entitlement_service
from src.platform.entitlements.service import EntitlementService
from src.platform.workspace.local_service import LocalWorkspaceService
from src.version_engine.bootstrap.dependencies import (
    get_product_operation_adapter,
    get_version_write_command_service,
)

router = APIRouter(
    prefix="/workspace",
    tags=["workspace"],
)


# ============================================================
# Request/Response Models
# ============================================================


class CreateWorkspaceRequest(BaseModel):
    project_id: str
    agent_id: str | None = None


class CreateWorkspaceResponse(BaseModel):
    agent_id: str
    workspace_path: str
    base_commit_id: str | None = None
    mount_command: str


class CompleteWorkspaceResponse(BaseModel):
    agent_id: str
    total_files: int
    committed: int
    conflict_count: int
    strategies: list[str] = []


class WorkspaceStatusResponse(BaseModel):
    agent_id: str
    exists: bool
    workspace_path: str | None = None
    base_commit_id: str | None = None


# ============================================================
# Create Workspace
# ============================================================


@router.post("/create", response_model=ApiResponse[CreateWorkspaceResponse])
async def create_workspace(
    request: CreateWorkspaceRequest,
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    entitlement_service: EntitlementService = Depends(get_entitlement_service),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    grant = authorization.authorize(
        request.project_id, current_user.user_id, ProjectAction.AGENT_RUN
    )
    entitlement_service.require_feature(grant.org_id, "remote_workspace.create")
    guard_unmetered_hosted_runtime("remote_workspace.create")
    info = await LocalWorkspaceService().create(request.project_id, request.agent_id, ops, grant)
    return ApiResponse.success(
        data=CreateWorkspaceResponse(
            agent_id=info.agent_id,
            workspace_path=info.path,
            base_commit_id=info.base_commit_id,
            mount_command=f"docker run -v {info.path}:/workspace your-agent-image",
        )
    )


# ============================================================
# Trigger merge after Agent completes (via VersionAdminService)
# ============================================================


@router.post("/{agent_id}/complete", response_model=ApiResponse[CompleteWorkspaceResponse])
async def complete_workspace(
    agent_id: str,
    project_id: str = Query(..., description="Project ID"),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
    commands: VersionWriteCommandService = Depends(get_version_write_command_service),
):
    authorization.authorize(project_id, current_user.user_id, ProjectAction.CONTENT_WRITE)
    result = await LocalWorkspaceService().complete(project_id, agent_id, commands)
    return ApiResponse.success(
        data=CompleteWorkspaceResponse(**asdict(result)),
        message="No changes detected" if result.total_files == 0 else "success",
    )


# ============================================================
# View Workspace Status
# ============================================================


@router.get("/{agent_id}/status", response_model=ApiResponse[WorkspaceStatusResponse])
def workspace_status(
    agent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
):
    info = LocalWorkspaceService().info(agent_id)

    # A workspace is bound to a project — only members of that project may
    # observe its status/path. Without this any authenticated user could probe
    # arbitrary agent_ids and learn another tenant's workspace paths.
    if info is not None:
        authorization.authorize(info.project_id, current_user.user_id, ProjectAction.AGENT_READ)

    if LocalWorkspaceService.exists(info):
        return ApiResponse.success(
            data=WorkspaceStatusResponse(
                agent_id=agent_id,
                exists=True,
                workspace_path=info.path,
                base_commit_id=info.base_commit_id,
            )
        )

    return ApiResponse.success(
        data=WorkspaceStatusResponse(
            agent_id=agent_id,
            exists=False,
        )
    )
