"""Human-authorized live workspace API. Published content routes stay Git-native."""

import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.platform.access.composition import get_workbench_service
from src.platform.auth.dependencies import get_current_user
from src.platform.sandbox import binding_profile, default_profile, resolve_features
from src.platform.workspace.errors import WorkspaceError, opening_failure
from src.utils.request_context import request_id_var

logger = logging.getLogger(__name__)


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
        "sandbox": public_sandbox(row.get("binding")),
    }


def public_sandbox(binding):
    """Profile and Feature policy for client entry points; a workspace not yet
    bound is shown with the Profile it would receive."""
    try:
        profile = binding_profile(binding) if binding else default_profile()
    except ValueError:
        # A Profile unknown to this deployment offers no Feature entry points.
        return {"profile": (binding or {}).get("profile"), "features": []}
    return {"profile": profile.id, "features": resolve_features(profile)}


router = APIRouter(prefix="/api/v1/projects/{project_id}/workbench", tags=["workbench"])


@asynccontextmanager
async def _opening(stage, project_id):
    """Every way an opening stops reaches the caller with a code, and is counted."""
    try:
        yield
    except Exception as exc:
        failure = exc if isinstance(exc, WorkspaceError) else opening_failure(exc)
        # An error with its own HTTP contract (billing, auth) keeps it; only an
        # unclassified one is a server error.
        source = failure if failure is not None else exc
        status = getattr(source, "status_code", 500)
        detail = getattr(source, "detail", None)
        detail = detail if isinstance(detail, dict) else {}
        logger.warning(
            "workbench_open_failed",
            extra={
                "workbench_open": {
                    "stage": stage,
                    "project_id": project_id,
                    "code": detail.get("code") or ("unclassified" if status >= 500 else None),
                    "status": status,
                    "retryable": detail.get("retryable"),
                    "error_type": type(exc).__name__,
                    "request_id": request_id_var.get(),
                }
            },
        )
        if failure is None or failure is exc:
            raise
        raise failure from exc


class FileOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=4096)
    content_base64: str = Field(max_length=12 * 1024 * 1024)
    expected_revision: str | None


@router.get("")
async def status(
    project_id: str,
    response: Response,
    wait_seconds: int = Query(default=0, ge=0, le=20),
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    response.headers["Cache-Control"] = "no-store"
    async with _opening("status", project_id):
        row, root = await service.opening.status(
            user.user_id, project_id, wait_seconds=wait_seconds
        )
    return {"workspace": public_workbench(row), **(root or {})}


@router.post("")
async def open_workspace(
    project_id: str,
    response: Response,
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    async with _opening("open", project_id):
        row = await service.request_open(user.user_id, project_id)
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


class WorkspaceGitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: UUID


class WorkspaceSyncRequest(WorkspaceGitRequest):
    source_ref: str | None = Field(default=None, max_length=1024)


async def _bound_workspace(service, user, project_id, request):
    row, _ = await service.bound(user.user_id, project_id, str(request.generation), write=True)
    return row


@router.post("/sync")
async def sync_workspace(
    project_id: str,
    request: WorkspaceSyncRequest,
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    """Bring the Project's branch, or an Agent's branch, into the Workspace."""
    row = await _bound_workspace(service, user, project_id, request)
    return await service.git.sync(row, request.source_ref)


@router.post("/sync/finish")
async def finish_sync(
    project_id: str,
    request: WorkspaceGitRequest,
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    row = await _bound_workspace(service, user, project_id, request)
    return await service.git.finish(row)


@router.post("/sync/abort")
async def abort_sync(
    project_id: str,
    request: WorkspaceGitRequest,
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    row = await _bound_workspace(service, user, project_id, request)
    return await service.git.abort(row)


@router.post("/publish")
async def publish_workspace(
    project_id: str,
    request: WorkspaceGitRequest,
    user=Depends(get_current_user),
    service=Depends(get_workbench_service),
):
    """Save the Workspace's work and move the Project's branch to it."""
    row = await _bound_workspace(service, user, project_id, request)
    return await service.git.publish(row)


def workspace_error_handler(request, error: WorkspaceError):
    """Preserve the existing HTTP error contract at the adapter boundary."""
    from src.exception_handler import http_exception_handler

    return http_exception_handler(request, HTTPException(error.status_code, error.detail))
