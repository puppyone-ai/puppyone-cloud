"""
Tool management API

Provides CRUD operations for public.tool.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from src.common_schemas import ApiResponse
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.organization.dependencies import resolve_org_id, resolve_org_ids
from src.tool.dependencies import get_tool_service
from src.tool.schemas import ToolCreate, ToolOut, ToolUpdate
from src.tool.service import ToolCreateParams, ToolService

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get(
    "/",
    response_model=ApiResponse[list[ToolOut]],
    summary="Get the current user's Tool list",
    status_code=status.HTTP_200_OK,
)
def list_tools(
    org_id: str | None = Query(None, description="Organization ID (optional, falls back to user's orgs)"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    tool_service: ToolService = Depends(get_tool_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    oids = resolve_org_ids(org_id, current_user.user_id)
    all_tools: list = []
    for oid in oids:
        all_tools.extend(
            tool_service.list_org_tools(
                current_user.user_id, oid, skip=skip, limit=limit
            )
        )
    return ApiResponse.success(data=all_tools, message="Tool list retrieved successfully")


@router.get(
    "/by-path/{path:path}",
    response_model=ApiResponse[list[ToolOut]],
    summary="Get Tool list under a specific path",
    status_code=status.HTTP_200_OK,
)
def list_tools_by_path(
    path: str,
    org_id: str | None = Query(None, description="Organization ID (optional)"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=1000),
    tool_service: ToolService = Depends(get_tool_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    resolved = resolve_org_id(org_id, current_user.user_id)
    tools = tool_service.list_org_tools_by_path(
        current_user.user_id,
        resolved,
        path=path,
        skip=skip,
        limit=limit,
    )
    return ApiResponse.success(data=tools, message="Tool list retrieved successfully")


@router.get(
    "/by-project/{project_id}",
    response_model=ApiResponse[list[ToolOut]],
    summary="Get Tool list under a specific project_id (aggregated across all nodes)",
    status_code=status.HTTP_200_OK,
)
def list_tools_by_project_id(
    project_id: str,
    org_id: str | None = Query(None, description="Organization ID (optional)"),
    tool_service: ToolService = Depends(get_tool_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    resolved = resolve_org_id(org_id, current_user.user_id)
    tools = tool_service.list_org_tools_by_project_id(
        current_user.user_id, resolved, project_id=project_id
    )
    return ApiResponse.success(data=tools, message="Tool list retrieved successfully")


@router.post(
    "/",
    response_model=ApiResponse[ToolOut],
    summary="Create Tool",
    description=(
        "Create a Tool. Search Tools were retired with the vector search backend.\n"
    ),
    status_code=status.HTTP_201_CREATED,
)
def create_tool(
    payload: ToolCreate,
    org_id: str | None = Query(None, description="Organization ID (optional)"),
    tool_service: ToolService = Depends(get_tool_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    resolved = resolve_org_id(org_id, current_user.user_id)
    metadata = (
        payload.metadata if isinstance(payload.metadata, dict) else payload.metadata
    )
    tool = tool_service.create(
        params=ToolCreateParams(
            org_id=resolved,
            created_by=current_user.user_id,
            path=payload.path,
            json_path=payload.json_path,
            type=payload.type,
            name=payload.name,
            alias=payload.alias,
            description=payload.description,
            input_schema=payload.input_schema,
            output_schema=payload.output_schema,
            metadata=metadata,
            category=payload.category,
            script_type=payload.script_type,
            script_content=payload.script_content,
        )
    )

    return ApiResponse.success(data=tool, message="Tool created successfully")


@router.get(
    "/{tool_id}",
    response_model=ApiResponse[ToolOut],
    summary="Get Tool",
    status_code=status.HTTP_200_OK,
)
def get_tool(
    tool_id: str,
    tool_service: ToolService = Depends(get_tool_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    tool = tool_service.get_by_id_with_access_check(tool_id, current_user.user_id)
    return ApiResponse.success(data=tool, message="Tool retrieved successfully")


@router.put(
    "/{tool_id}",
    response_model=ApiResponse[ToolOut],
    summary="Update Tool",
    status_code=status.HTTP_200_OK,
)
def update_tool(
    tool_id: str,
    payload: ToolUpdate,
    tool_service: ToolService = Depends(get_tool_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    # Only update fields actually passed in the request body; unset fields are not affected
    patch = payload.model_dump(exclude_unset=True)
    tool = tool_service.update(
        tool_id=tool_id, user_id=current_user.user_id, patch=patch
    )
    return ApiResponse.success(data=tool, message="Tool updated successfully")


@router.delete(
    "/{tool_id}",
    response_model=ApiResponse[None],
    summary="Delete Tool",
    status_code=status.HTTP_200_OK,
)
def delete_tool(
    tool_id: str,
    tool_service: ToolService = Depends(get_tool_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    tool_service.delete(tool_id, current_user.user_id)
    return ApiResponse.success(data=None, message="Tool deleted successfully")
