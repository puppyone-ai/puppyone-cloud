"""One directory of every place the signed-in user's folders live.

Hosts are the user's own computers (``device:<id>``) and their hosted
workspaces (``workspace:<project_id>``). The file API here serves hosted
workspaces (Project grant and Workspace kernel); a computer's folders are
listed but their content is reached only end to end, inside SSH that ends on
that computer, so their file routes answer 410. Composing both modules is why
this lives in the access layer.
"""

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.access.composition import get_workbench_service
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.hosts.router import get_host_service, require_host_devices_enabled
from src.platform.organization.dependencies import resolve_org_ids
from src.platform.project.dependencies import get_project_service
from src.platform.workspace.errors import WorkspaceError
from src.shared_types.grants import ProjectAction

MAX_WORKSPACES = 50

REVISION = r"^[a-f0-9]{64}$"


class FileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    content_base64: str = Field(max_length=12 * 1024 * 1024)
    # The revision that was read, or null to create a file that must not exist yet.
    expected_revision: str | None = Field(pattern=REVISION)


# Named apart from the workbench's own models: OpenAPI schema names are global.
class HostDirectoryOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["mkdir", "move", "remove"]
    path: str = Field(min_length=1, max_length=4096)
    destination: str | None = Field(default=None, min_length=1, max_length=4096)

router = APIRouter(prefix="/api/v1/host-directory", tags=["hosts"],
                   dependencies=[Depends(require_host_devices_enabled)])


class HostRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["device", "workspace"]
    id: UUID


def parse_host(value: str) -> HostRef:
    kind, _, identity = value.partition(":")
    try:
        return HostRef(kind=kind, id=UUID(identity))
    except Exception as exc:
        raise HTTPException(404, "Not found") from exc


def _workspace_host(project, grant, row) -> dict:
    project_id = str(project.id)
    capabilities = ["folder.read"]
    if grant.allows(ProjectAction.CONTENT_WRITE):
        capabilities.append("folder.write")
    return {"kind": "hosted-workspace", "id": f"workspace:{project_id}", "name": project.name,
            "online": row.get("state") == "running", "state": row.get("state"),
            "generation": row.get("generation"), "project_id": project_id,
            # A hosted workspace has one folder: its Project root.
            "folders": [{"id": project_id, "name": project.name, "capabilities": capabilities,
                         "state": "active"}]}


@router.get("")
async def host_directory(response: Response, user: CurrentUser = Depends(get_current_user),
                         hosts=Depends(get_host_service), projects=Depends(get_project_service),
                         authorization=Depends(get_authorization_service),
                         workbench=Depends(get_workbench_service)):
    response.headers["Cache-Control"] = "no-store"
    devices = await run_blocking(hosts.catalog, user.user_id)
    listed = [{**device, "kind": "device", "device_id": device["id"], "id": f"device:{device['id']}"}
              for device in devices]
    candidates = []
    for organization in await run_blocking(resolve_org_ids, None, user.user_id):
        candidates.extend(await run_blocking(projects.get_by_org_id, organization))
    accessible = await run_blocking(authorization.filter_accessible, candidates, user.user_id)
    for project, grant in accessible[:MAX_WORKSPACES]:
        # Observe only: listing never creates or wakes a workspace.
        try:
            row, _root = await workbench.opening.status(user.user_id, str(project.id))
        except (HTTPException, WorkspaceError):
            continue
        if row:
            listed.append(_workspace_host(project, grant, row))
    return {"hosts": listed, "gateway_url": settings.HOST_GATEWAY_PUBLIC_URL.rstrip("/") or None}


def _folder_path(host: HostRef, folder: UUID):
    # A hosted workspace's only folder is its Project root.
    if host.kind == "workspace" and folder != host.id:
        raise HTTPException(404, "Not found")


def _end_to_end_only(host: HostRef):
    """A computer's folders are reached only inside SSH that ends on that computer (the Desktop), never here."""
    if host.kind == "device":
        raise HTTPException(410, {"code": "device_folders_are_end_to_end"})


async def _operate(host: HostRef, folder: UUID, user: CurrentUser, generation: UUID | None,
                   action: str, operation: dict, workbench):
    _folder_path(host, folder)
    _end_to_end_only(host)
    if generation is None:
        raise HTTPException(422, "A hosted workspace needs its generation.")
    return await workbench.file_operation(user.user_id, str(host.id), str(generation),
                                          {"action": action, **operation})


@router.get("/{host}/folders/{folder_id}/files")
async def list_files(host: str, folder_id: UUID, response: Response,
                     path: str = Query(default="", max_length=4096), generation: UUID | None = None,
                     user: CurrentUser = Depends(get_current_user),
                     workbench=Depends(get_workbench_service)):
    response.headers["Cache-Control"] = "no-store"
    return await _operate(parse_host(host), folder_id, user, generation, "list", {"path": path}, workbench)


@router.get("/{host}/folders/{folder_id}/file")
async def read_file(host: str, folder_id: UUID, response: Response,
                    path: str = Query(min_length=1, max_length=4096), generation: UUID | None = None,
                    user: CurrentUser = Depends(get_current_user),
                    workbench=Depends(get_workbench_service)):
    response.headers["Cache-Control"] = "no-store"
    return await _operate(parse_host(host), folder_id, user, generation, "read", {"path": path}, workbench)


@router.put("/{host}/folders/{folder_id}/file")
async def write_file(host: str, folder_id: UUID, body: FileWrite, generation: UUID | None = None,
                     user: CurrentUser = Depends(get_current_user),
                     workbench=Depends(get_workbench_service)):
    return await _operate(parse_host(host), folder_id, user, generation, "write", body.model_dump(),
                          workbench)


@router.post("/{host}/folders/{folder_id}/files")
async def change_files(host: str, folder_id: UUID, body: HostDirectoryOperation, generation: UUID | None = None,
                       user: CurrentUser = Depends(get_current_user),
                       workbench=Depends(get_workbench_service)):
    if (body.action == "move") != (body.destination is not None):
        raise HTTPException(422, "A move needs a destination; other operations do not.")
    operation = body.model_dump(exclude={"action"}, exclude_none=True)
    return await _operate(parse_host(host), folder_id, user, generation, body.action, operation, workbench)


@router.get("/{host}/folders/{folder_id}/events")
async def folder_events(host: str, folder_id: UUID, request: Request, generation: UUID | None = None,
                        user: CurrentUser = Depends(get_current_user),
                        workbench=Depends(get_workbench_service)):
    target = parse_host(host)
    _folder_path(target, folder_id)
    _end_to_end_only(target)
    if generation is None:
        raise HTTPException(422, "A hosted workspace needs its generation.")
    await workbench.bound(user.user_id, str(target.id), str(generation))
    stream = _workspace_events(workbench, user.user_id, str(target.id), str(generation))
    first = await anext(stream)

    def frame(event):
        if event is None:
            return ": keepalive\n\n"
        if "unavailable" in event:
            return "event: unavailable\ndata: " + json.dumps(event["unavailable"]) + "\n\n"
        return "event: change\ndata: " + json.dumps({"paths": event.get("paths") or [],
                                                      "reset": bool(event.get("reset"))}) + "\n\n"

    async def sse():
        try:
            yield frame(first)
            async for event in stream:
                if await request.is_disconnected():
                    return
                yield frame(event)
                if event is not None and "unavailable" in event:
                    return
        finally:
            await stream.aclose()

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


async def _workspace_events(workbench, user, project, generation):
    """Hosted workspace changes in the same shape as a device folder's."""
    yield {"paths": [], "reset": True}
    try:
        async for value in workbench.events(user, project, generation):
            yield None if value is None else {"paths": value.get("paths") or [], "reset": bool(value.get("reset"))}
    except (HTTPException, WorkspaceError):
        yield {"unavailable": {}}
    except ConnectionError:
        yield {"unavailable": {"retryable": True}}
