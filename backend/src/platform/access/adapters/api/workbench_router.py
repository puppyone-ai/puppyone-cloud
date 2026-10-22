"""Human-authorized live workspace API. Published content routes stay Git-native."""

import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.platform.access.composition import build_workbench_service
from src.platform.auth.dependencies import get_current_user
from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.execution.runtime_contract import WorkspaceUnavailable
from src.version_engine.bootstrap.dependencies import get_version_engine_container


def public_workbench(row):
    if row is None:
        return None
    expired = bool(
        row.get("open_requested")
        and row.get("open_deadline")
        and datetime.fromisoformat(row["open_deadline"]) <= datetime.now(UTC)
    )
    return {
        **{key: row[key] for key in ("id", "generation", "project_id", "state", "target_ref")},
        "operation": {
            "pending": row.get("open_requested", False) and not expired,
            "phase": "failed" if expired else row.get("operation_phase"),
            "deadline": row.get("open_deadline"),
            "error": "workbench_start_timeout" if expired else row.get("operation_error"),
            "retryable": expired or row.get("operation_retryable", False),
        },
    }


router = APIRouter(prefix="/api/v1/projects/{project_id}/workbench", tags=["workbench"])


async def get_workbench_service(request: Request, engine=Depends(get_version_engine_container)):
    service = getattr(request.app.state, "workbench_service", None)
    if service is None:
        service = build_workbench_service(engine=engine)
        request.app.state.workbench_service = service
    return service


class FileOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=4096)
    content_base64: str = Field(max_length=12 * 1024 * 1024)
    expected_revision: str | None


@router.get("")
async def status(
    project_id: str, user=Depends(get_current_user), service=Depends(get_workbench_service)
):
    return {"workspace": public_workbench(await service.current(user.user_id, project_id))}


@router.post("")
async def open_workspace(
    project_id: str,
    response: Response,
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    try:
        row = await service.request_open(user.user_id, project_id)
    except WorkspaceUnavailable as exc:
        raise HTTPException(503, {"code": exc.code, "retryable": exc.retryable}) from exc
    response.status_code = 200 if row["state"] == "running" else 202
    return public_workbench(row)


@router.get("/files")
async def list_files(
    project_id: str,
    generation: UUID,
    path: str = Query(default="", max_length=4096),
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    return await service.file_operation(
        user.user_id, project_id, str(generation), {"action": "list", "path": path}
    )


@router.get("/file")
async def read_file(
    project_id: str,
    generation: UUID,
    path: str = Query(max_length=4096),
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    return await service.file_operation(
        user.user_id, project_id, str(generation), {"action": "read", "path": path}
    )


@router.put("/file")
async def write_file(
    project_id: str,
    generation: UUID,
    body: FileOperation,
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    return await service.file_operation(
        user.user_id, project_id, str(generation), {"action": "write", **body.model_dump()}
    )


class DirectoryOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["mkdir", "move", "remove"]
    path: str = Field(min_length=1, max_length=4096)
    destination: str | None = Field(default=None, max_length=4096)


@router.post("/files")
async def change_files(
    project_id: str,
    generation: UUID,
    body: DirectoryOperation,
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    return await service.file_operation(
        user.user_id, project_id, str(generation), body.model_dump()
    )


@router.get("/events")
async def events(
    project_id: str,
    generation: UUID,
    request: Request,
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    await service.bound(user.user_id, project_id, str(generation))

    async def stream():
        try:
            async for value in service.events(user.user_id, project_id, str(generation)):
                if await request.is_disconnected():
                    return
                yield (
                    ": keepalive\n\n"
                    if value is None
                    else "event: change\ndata: " + json.dumps(value) + "\n\n"
                )
        except (HTTPException, WorkspaceError):
            yield "event: unavailable\ndata: {}\n\n"
        except ConnectionError:
            yield 'event: unavailable\ndata: {"retryable":true}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def workspace_error_handler(request, error: WorkspaceError):
    """Preserve the existing HTTP error contract at the adapter boundary."""
    from src.exception_handler import http_exception_handler

    return http_exception_handler(request, HTTPException(error.status_code, error.detail))
