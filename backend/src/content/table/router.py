
from fastapi import APIRouter, Depends, Query, status

from src.common_schemas import ApiResponse
from src.content.table.dependencies import (
    get_table_service,
    get_verified_table,
    get_writable_table,
)
from src.content.table.models import Table
from src.content.table.schemas import (
    ContextDataCreate,
    ContextDataDelete,
    ContextDataGet,
    ContextDataUpdate,
    ProjectWithTables,
    TableCreate,
    TableOut,
    TableUpdate,
)
from src.content.table.service import TableService
from src.exceptions import ErrorCode, NotFoundException
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.organization.dependencies import resolve_org_ids
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService

router = APIRouter(
    prefix="/tables",
    tags=["tables"],
    responses={
        404: {"description": "Resource not found"},
        500: {"description": "Internal server error"},
    },
)


@router.get(
    "/",
    response_model=ApiResponse[list[ProjectWithTables]],
    summary="Get all projects and their tables",
    status_code=status.HTTP_200_OK,
)
def list_tables(
    org_id: str | None = Query(None, description="Organization ID"),
    table_service: TableService = Depends(get_table_service),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
):
    oids = resolve_org_ids(org_id, current_user.user_id)
    all_results = []
    for oid in oids:
        rows = table_service.get_projects_with_tables_by_org_id(oid)
        all_results.extend(
            row
            for row in rows
            if authorization.allows(
                row.id, current_user.user_id, ProjectAction.PROJECT_READ
            )
        )
    return ApiResponse.success(data=all_results, message="Projects and tables list retrieved successfully")


@router.get(
    "/{table_id}",
    response_model=ApiResponse[TableOut],
    summary="Get single table details",
    status_code=status.HTTP_200_OK,
)
def get_table(
    table: Table = Depends(get_verified_table),
):
    return ApiResponse.success(data=table, message="Table retrieved successfully")


@router.post(
    "/",
    response_model=ApiResponse[TableOut],
    summary="Create a new table",
    status_code=status.HTTP_201_CREATED,
)
async def create_table(
    payload: TableCreate,
    table_service: TableService = Depends(get_table_service),
    current_user: CurrentUser = Depends(get_current_user),
    authorization: AuthorizationService = Depends(get_authorization_service),
):
    authorization.authorize(
        payload.project_id, current_user.user_id, ProjectAction.CONTENT_WRITE
    )

    table = await table_service.create(
        user_id=current_user.user_id,
        name=payload.name,
        description=payload.description,
        data=payload.data or {},
        project_id=payload.project_id,
    )
    return ApiResponse.success(data=table, message="Table created successfully")


@router.put(
    "/{table_id}",
    response_model=ApiResponse[TableOut],
    summary="Update table information",
    status_code=status.HTTP_200_OK,
)
async def update_table(
    table: Table = Depends(get_writable_table),
    payload: TableUpdate = ...,
    table_service: TableService = Depends(get_table_service),
):
    updated_table = await table_service.update(
        table_id=table.id,
        name=payload.name,
        description=payload.description,
        data=payload.data,
    )
    return ApiResponse.success(data=updated_table, message="Table updated successfully")


@router.delete(
    "/{table_id}",
    response_model=ApiResponse[None],
    summary="Delete table",
    status_code=status.HTTP_200_OK,
)
async def delete_table(
    table: Table = Depends(get_writable_table),
    table_service: TableService = Depends(get_table_service),
):
    await table_service.delete(table.id)
    return ApiResponse.success(message="Table deleted successfully")


@router.post(
    "/{table_id}/data",
    response_model=ApiResponse[ContextDataGet],
    summary="Create data in table",
    description='Create new data items in table data via JSON pointer path. Path uses RFC 6901 format. Use empty string "" for root path.',
    status_code=status.HTTP_201_CREATED,
)
async def create_context_data(
    table: Table = Depends(get_writable_table),
    payload: ContextDataCreate = ...,
    table_service: TableService = Depends(get_table_service),
):
    data = await table_service.create_context_data(
        table_id=table.id,
        mounted_json_pointer_path=payload.mounted_json_pointer_path,
        elements=[{"key": e.key, "content": e.content} for e in payload.elements],
    )
    return ApiResponse.success(data=ContextDataGet(data=data), message="Data created successfully")


@router.get(
    "/{table_id}/data",
    response_model=ApiResponse[ContextDataGet],
    summary="Get data from table",
    description='Get data via JSON pointer path. Path uses RFC 6901 format. Use empty string "" for root path.',
    status_code=status.HTTP_200_OK,
)
def get_context_data(
    table: Table = Depends(get_verified_table),
    json_pointer_path: str | None = Query(
        default="",
        description='JSON pointer path (RFC 6901)',
        min_length=0,
        examples=["", "/users", "/users/123"],
    ),
    table_service: TableService = Depends(get_table_service),
):
    if json_pointer_path is None:
        json_pointer_path = ""

    data = table_service.get_context_data(
        table_id=table.id, json_pointer_path=json_pointer_path
    )
    return ApiResponse.success(data=ContextDataGet(data=data), message="Data retrieved successfully")


@router.put(
    "/{table_id}/data",
    response_model=ApiResponse[ContextDataGet],
    summary="Update data in table",
    description='Update existing data items via JSON pointer path. Path uses RFC 6901 format. Use empty string "" for root path.',
    status_code=status.HTTP_200_OK,
)
async def update_context_data(
    table: Table = Depends(get_writable_table),
    payload: ContextDataUpdate = ...,
    table_service: TableService = Depends(get_table_service),
):
    data = await table_service.update_context_data(
        table_id=table.id,
        json_pointer_path=payload.json_pointer_path,
        elements=[{"key": e.key, "content": e.content} for e in payload.elements],
    )
    return ApiResponse.success(data=ContextDataGet(data=data), message="Data updated successfully")


@router.delete(
    "/{table_id}/data",
    response_model=ApiResponse[ContextDataGet],
    summary="Delete data from table",
    description='Delete data via JSON pointer path. Path uses RFC 6901 format. Use empty string "" for root path.',
    status_code=status.HTTP_200_OK,
)
async def delete_context_data(
    table: Table = Depends(get_writable_table),
    payload: ContextDataDelete = ...,
    table_service: TableService = Depends(get_table_service),
):
    data = await table_service.delete_context_data(
        table_id=table.id,
        json_pointer_path=payload.json_pointer_path,
        keys=payload.keys,
    )
    return ApiResponse.success(data=ContextDataGet(data=data), message="Data deleted successfully")
