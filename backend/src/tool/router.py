"""
Tool management API

Provides CRUD operations for public.tool.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from src.common_schemas import ApiResponse
from src.config import settings
from src.infra.s3.service import S3Service
from src.infra.search.dependencies import get_search_service
from src.infra.search.index_task import SearchIndexTaskOut, SearchIndexTaskUpsert
from src.infra.search.index_task_repository import SearchIndexTaskRepository
from src.infra.search.service import SearchService
from src.infra.supabase.client import SupabaseClient
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.organization.dependencies import resolve_org_id, resolve_org_ids
from src.tool.dependencies import get_tool_service
from src.tool.schemas import ToolCreate, ToolOut, ToolUpdate
from src.tool.service import ToolCreateParams, ToolService
from src.utils.logger import log_error, log_info

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
        "Create a Tool.\n\n"
        "Note: Search Tool index building has been moved to a separate async API (see `/tools/search`).\n"
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


async def _run_search_indexing_background(
    *,
    repo: SearchIndexTaskRepository,
    search_service: SearchService,
    tool_id: str,
    user_id: str,
    project_id: str,
    path: str,
    json_path: str,
) -> None:
    """
    Background indexing executor: writes index task status and logs (does not throw to the requester).
    """
    now = dt.datetime.now(tz=dt.UTC)
    log_info(
        f"[search_index] background task accepted: tool_id={tool_id} project_id={project_id} path={path} json_path='{json_path}'"
    )
    try:
        await asyncio.to_thread(
            repo.upsert,
            SearchIndexTaskUpsert(
                tool_id=tool_id,
                user_id=user_id,
                project_id=project_id,
                path=path,
                json_path=json_path or "",
                status="indexing",
                started_at=now,
                finished_at=None,
                nodes_count=None,
                chunks_count=None,
                indexed_chunks_count=None,
                last_error=None,
            ),
        )
    except Exception as e:
        log_error(
            f"[search_index] failed to mark indexing: tool_id={tool_id} path={path} json_path='{json_path}' err={e}"
        )

    try:
        log_info(
            f"[search_index] start: tool_id={tool_id} project_id={project_id} path={path} json_path='{json_path}'"
        )
        stats = await asyncio.wait_for(
            search_service.index_scope(
                project_id=project_id,
                path=path,
                user_id=user_id,
                json_path=json_path or "",
            ),
            timeout=float(settings.SEARCH_INDEX_TIMEOUT_SECONDS),
        )
        finished = dt.datetime.now(tz=dt.UTC)
        await asyncio.to_thread(
            repo.upsert,
            SearchIndexTaskUpsert(
                tool_id=tool_id,
                user_id=user_id,
                project_id=project_id,
                path=path,
                json_path=json_path or "",
                status="ready",
                started_at=now,
                finished_at=finished,
                nodes_count=int(stats.nodes_count),
                chunks_count=int(stats.chunks_count),
                indexed_chunks_count=int(stats.indexed_chunks_count),
                last_error=None,
            ),
        )
        log_info(
            f"[search_index] done: tool_id={tool_id} nodes={stats.nodes_count} chunks={stats.chunks_count}"
        )
    except TimeoutError:
        finished = dt.datetime.now(tz=dt.UTC)
        msg = f"index_scope timeout after {settings.SEARCH_INDEX_TIMEOUT_SECONDS}s"
        try:
            await asyncio.to_thread(
                repo.upsert,
                SearchIndexTaskUpsert(
                    tool_id=tool_id,
                    user_id=user_id,
                    project_id=project_id,
                    path=path,
                    json_path=json_path or "",
                    status="error",
                    started_at=now,
                    finished_at=finished,
                    last_error=msg,
                ),
            )
        except Exception as e:
            log_error(
                f"[search_index] failed to write timeout status: tool_id={tool_id} err={e}"
            )
        log_error(
            f"[search_index] timeout: tool_id={tool_id} project_id={project_id} path={path} json_path='{json_path}'"
        )
    except Exception as e:
        finished = dt.datetime.now(tz=dt.UTC)
        err = str(e)[:500]
        try:
            await asyncio.to_thread(
                repo.upsert,
                SearchIndexTaskUpsert(
                    tool_id=tool_id,
                    user_id=user_id,
                    project_id=project_id,
                    path=path,
                    json_path=json_path or "",
                    status="error",
                    started_at=now,
                    finished_at=finished,
                    last_error=err,
                ),
            )
        except Exception as e2:
            log_error(
                f"[search_index] failed to write error status: tool_id={tool_id} err={e2}"
            )
        log_error(
            f"[search_index] failed: tool_id={tool_id} project_id={project_id} path={path} json_path='{json_path}' err={e}"
        )


async def _run_folder_search_indexing_background(
    *,
    repo: SearchIndexTaskRepository,
    search_service: SearchService,
    s3_service: S3Service,
    tool_id: str,
    user_id: str,
    project_id: str,
    folder_path: str,
) -> None:
    """
    Background indexing executor for folder search.
    """
    now = dt.datetime.now(tz=dt.UTC)
    log_info(
        f"[folder_search_index] background task accepted: tool_id={tool_id} "
        f"project_id={project_id} folder_path={folder_path}"
    )

    # Mark as indexing
    try:
        await asyncio.to_thread(
            repo.upsert,
            SearchIndexTaskUpsert(
                tool_id=tool_id,
                user_id=user_id,
                project_id=project_id,
                path=folder_path,
                json_path="",
                status="indexing",
                started_at=now,
                finished_at=None,
                folder_path=folder_path,
                total_files=None,
                indexed_files=0,
                last_error=None,
            ),
        )
    except Exception as e:
        log_error(
            f"[folder_search_index] failed to mark indexing: tool_id={tool_id} err={e}"
        )

    # Progress callback to update task status
    def update_progress(indexed_files: int, total_files: int) -> None:
        try:
            repo.upsert(
                SearchIndexTaskUpsert(
                    tool_id=tool_id,
                    user_id=user_id,
                    project_id=project_id,
                    path=folder_path,
                    json_path="",
                    status="indexing",
                    started_at=now,
                    folder_path=folder_path,
                    total_files=total_files,
                    indexed_files=indexed_files,
                )
            )
        except Exception as e:
            log_error(f"[folder_search_index] progress update error: {e}")

    try:
        log_info(
            f"[folder_search_index] start: tool_id={tool_id} folder_path={folder_path}"
        )
        stats = await asyncio.wait_for(
            search_service.index_folder(
                project_id=project_id,
                folder_path=folder_path,
                user_id=user_id,
                s3_service=s3_service,
                progress_callback=update_progress,
            ),
            timeout=float(settings.SEARCH_INDEX_TIMEOUT_SECONDS),
        )
        finished = dt.datetime.now(tz=dt.UTC)
        await asyncio.to_thread(
            repo.upsert,
            SearchIndexTaskUpsert(
                tool_id=tool_id,
                user_id=user_id,
                project_id=project_id,
                path=folder_path,
                json_path="",
                status="ready",
                started_at=now,
                finished_at=finished,
                nodes_count=int(stats.nodes_count),
                chunks_count=int(stats.chunks_count),
                indexed_chunks_count=int(stats.indexed_chunks_count),
                folder_path=folder_path,
                total_files=int(stats.total_files),
                indexed_files=int(stats.indexed_files),
                last_error=None,
            ),
        )
        log_info(
            f"[folder_search_index] done: tool_id={tool_id} files={stats.indexed_files}/{stats.total_files} "
            f"chunks={stats.chunks_count}"
        )
    except TimeoutError:
        finished = dt.datetime.now(tz=dt.UTC)
        msg = f"index_folder timeout after {settings.SEARCH_INDEX_TIMEOUT_SECONDS}s"
        try:
            await asyncio.to_thread(
                repo.upsert,
                SearchIndexTaskUpsert(
                    tool_id=tool_id,
                    user_id=user_id,
                    project_id=project_id,
                    path=folder_path,
                    json_path="",
                    status="error",
                    started_at=now,
                    finished_at=finished,
                    folder_path=folder_path,
                    last_error=msg,
                ),
            )
        except Exception as e:
            log_error(
                f"[folder_search_index] failed to write timeout status: tool_id={tool_id} err={e}"
            )
        log_error(
            f"[folder_search_index] timeout: tool_id={tool_id} folder_path={folder_path}"
        )
    except Exception as e:
        finished = dt.datetime.now(tz=dt.UTC)
        err = str(e)[:500]
        try:
            await asyncio.to_thread(
                repo.upsert,
                SearchIndexTaskUpsert(
                    tool_id=tool_id,
                    user_id=user_id,
                    project_id=project_id,
                    path=folder_path,
                    json_path="",
                    status="error",
                    started_at=now,
                    finished_at=finished,
                    folder_path=folder_path,
                    last_error=err,
                ),
            )
        except Exception as e2:
            log_error(
                f"[folder_search_index] failed to write error status: tool_id={tool_id} err={e2}"
            )
        log_error(
            f"[folder_search_index] failed: tool_id={tool_id} folder_path={folder_path} err={e}"
        )


@router.post(
    "/search",
    response_model=ApiResponse[ToolOut],
    summary="Create Search Tool (async indexing)",
    description=(
        "Create a `type=search` Tool, and asynchronously trigger indexing (chunking + embedding + upsert) after the response is returned.\n\n"
        "Supports two modes:\n"
        "- **JSON Search**: path points to a json-type node, indexes the JSON content of that node\n"
        "- **Folder Search**: path points to a folder-type node, indexes all json/markdown files under the folder\n\n"
        "Index status can be polled via `/tools/{tool_id}/search-index`."
    ),
    status_code=status.HTTP_201_CREATED,
)
def create_search_tool_async(
    payload: ToolCreate,
    background_tasks: BackgroundTasks,
    org_id: str | None = Query(None, description="Organization ID (optional)"),
    tool_service: ToolService = Depends(get_tool_service),
    search_service: SearchService = Depends(get_search_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    resolved = resolve_org_id(org_id, current_user.user_id)
    if (payload.type or "").strip() != "search":
        return ApiResponse.error(code=400, message="payload.type must be 'search'")

    if not payload.path:
        return ApiResponse.error(code=400, message="path is required for search tool")

    node = tool_service.get_path_with_access_check(current_user.user_id, payload.path)
    project_id = node.project_id
    is_folder_search = node.type == "folder"

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
            metadata=payload.metadata,
            category=payload.category,
            script_type=payload.script_type,
            script_content=payload.script_content,
        )
    )

    sb_client = SupabaseClient().get_client()
    repo = SearchIndexTaskRepository(sb_client)

    if is_folder_search:
        # Folder Search: index all files in the folder
        log_info(
            f"[search_index] folder search mode: tool_id={tool.id} folder_path={payload.path}"
        )

        # Write a pending record first (so the polling endpoint can get the status immediately)
        try:
            repo.upsert(
                SearchIndexTaskUpsert(
                    tool_id=str(tool.id),
                    user_id=str(current_user.user_id),
                    project_id=project_id,
                    path=str(payload.path),
                    json_path="",
                    status="pending",
                    started_at=None,
                    finished_at=None,
                    folder_path=str(payload.path),
                    total_files=None,
                    indexed_files=0,
                    last_error=None,
                )
            )
        except Exception as e:
            log_error(
                f"[search_index] failed to create folder task row: tool_id={tool.id} "
                f"folder_path={payload.path} err={e}"
            )

        # Create S3 service for reading markdown files
        s3_service = S3Service()

        background_tasks.add_task(
            _run_folder_search_indexing_background,
            repo=repo,
            search_service=search_service,
            s3_service=s3_service,
            tool_id=str(tool.id),
            user_id=str(current_user.user_id),
            project_id=project_id,
            folder_path=str(payload.path),
        )

        return ApiResponse.success(
            data=tool, message="Folder Search Tool created successfully (indexing triggered asynchronously)"
        )
    else:
        # JSON Search: existing behavior
        log_info(
            f"[search_index] json search mode: tool_id={tool.id} path={payload.path}"
        )

        # Write a pending record first (so the polling endpoint can get the status immediately)
        try:
            repo.upsert(
                SearchIndexTaskUpsert(
                    tool_id=str(tool.id),
                    user_id=str(current_user.user_id),
                    project_id=project_id,
                    path=str(payload.path),
                    json_path=payload.json_path or "",
                    status="pending",
                    started_at=None,
                    finished_at=None,
                    last_error=None,
                )
            )
        except Exception as e:
            log_error(
                f"[search_index] failed to create task row: tool_id={tool.id} "
                f"path={payload.path} json_path='{payload.json_path}' err={e}"
            )

        background_tasks.add_task(
            _run_search_indexing_background,
            repo=repo,
            search_service=search_service,
            tool_id=str(tool.id),
            user_id=str(current_user.user_id),
            project_id=project_id,
            path=str(payload.path),
            json_path=payload.json_path or "",
        )

        return ApiResponse.success(
            data=tool, message="Search Tool created successfully (indexing triggered asynchronously)"
        )


@router.get(
    "/{tool_id}/search-index",
    response_model=ApiResponse[SearchIndexTaskOut],
    summary="Query Search Tool index build status",
    description="Returns the index task status of this Search Tool.",
    status_code=status.HTTP_200_OK,
)
def get_search_index_status(
    tool_id: str,
    tool_service: ToolService = Depends(get_tool_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    import time

    t0 = time.perf_counter()
    log_info(f"[search-index-status] start: tool_id={tool_id}")

    t1 = time.perf_counter()
    tool = tool_service.get_by_id_with_access_check(tool_id, current_user.user_id)
    log_info(
        f"[search-index-status] get_tool: tool_id={tool_id} elapsed_ms={int((time.perf_counter() - t1) * 1000)}"
    )

    if (tool.type or "").strip() != "search":
        log_info(
            f"[search-index-status] not_search_tool: tool_id={tool_id} total_ms={int((time.perf_counter() - t0) * 1000)}"
        )
        return ApiResponse.error(code=400, message="Tool is not a search tool")

    t2 = time.perf_counter()
    sb_client = SupabaseClient().get_client()
    log_info(
        f"[search-index-status] get_supabase_client: tool_id={tool_id} elapsed_ms={int((time.perf_counter() - t2) * 1000)}"
    )

    t3 = time.perf_counter()
    repo = SearchIndexTaskRepository(sb_client)
    task = repo.get_by_tool_id(tool_id)
    log_info(
        f"[search-index-status] get_task: tool_id={tool_id} found={task is not None} elapsed_ms={int((time.perf_counter() - t3) * 1000)}"
    )

    if task is None:
        log_info(
            f"[search-index-status] task_not_found: tool_id={tool_id} total_ms={int((time.perf_counter() - t0) * 1000)}"
        )
        return ApiResponse.error(code=404, message="Search index task not found")

    out = SearchIndexTaskOut(
        tool_id=tool_id,
        status=task.status,
        started_at=task.started_at,
        finished_at=task.finished_at,
        nodes_count=task.nodes_count,
        chunks_count=task.chunks_count,
        indexed_chunks_count=task.indexed_chunks_count,
        last_error=task.last_error,
        # Folder search specific fields
        folder_path=task.folder_path,
        total_files=task.total_files,
        indexed_files=task.indexed_files,
    )
    log_info(
        f"[search-index-status] done: tool_id={tool_id} status={task.status} total_ms={int((time.perf_counter() - t0) * 1000)}"
    )
    return ApiResponse.success(data=out, message="Index status retrieved successfully")


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
