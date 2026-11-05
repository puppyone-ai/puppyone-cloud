"""Authenticated human API for the owner's devices and registered folders.

File requests carry only a folder id and a folder-relative path; the host alone
maps the folder to a local directory. Device sockets are served by the
``device_gateway`` role (see ``routing.py``), or in-process in development.
"""

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser

from .connection_auth import HostConnectionAuth
from .gateway import HostGateway
from .routing import ForwardingRoute, LocalRoute
from .service import HostService

REVISION = r"^[a-f0-9]{64}$"


def require_host_devices_enabled():
    if not settings.HOST_DEVICES_ENABLED:
        raise HTTPException(404, "Not found")


router = APIRouter(prefix="/api/v1/hosts", tags=["hosts"],
                   dependencies=[Depends(require_host_devices_enabled)])


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_key: str = Field(min_length=40, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    platform: Literal["darwin", "win32", "linux"]


class FolderRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: UUID  # client-chosen: a lost response retries the same identity
    name: str = Field(min_length=1, max_length=120)
    writable: bool = False


class HostFolderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    writable: bool
    expected_revision: int = Field(ge=1)


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


def get_host_service() -> HostService:
    return HostService()


def get_connection_auth() -> HostConnectionAuth:
    return HostConnectionAuth()


_gateway: HostGateway | None = None
_forwarding: ForwardingRoute | None = None


def forwarding() -> bool:
    return bool(settings.HOST_GATEWAY_INTERNAL_URL)


def get_gateway() -> HostGateway:
    global _gateway
    if _gateway is None:
        _gateway = HostGateway()
    return _gateway


def get_route() -> LocalRoute | ForwardingRoute:
    global _forwarding
    if not forwarding():
        return LocalRoute(get_gateway())
    if _forwarding is None:
        _forwarding = ForwardingRoute(settings.HOST_GATEWAY_INTERNAL_URL,
                                      settings.HOST_GATEWAY_INTERNAL_SECRET)
    return _forwarding


def _route(gateway: HostGateway = Depends(get_gateway)) -> LocalRoute | ForwardingRoute:
    return get_route() if forwarding() else LocalRoute(gateway)


@router.websocket("/devices/{device_id}/connect")
async def connect_device(websocket: WebSocket, device_id: UUID):
    # This path has no human JWT: the owner-bound, one-use challenge and
    # signature are verified before any file request can be delivered. When a
    # device_gateway is deployed, devices connect there and never to API workers.
    if not settings.HOST_DEVICES_ENABLED or forwarding():
        await websocket.close(code=4003)
        return
    await get_gateway().connected(device_id, websocket)


@router.get("/folders/{folder_id}/files")
async def list_folder(folder_id: UUID, response: Response,
                      path: str = Query(default="", max_length=4096),
                      user: CurrentUser = Depends(get_current_user), route=Depends(_route)):
    response.headers["Cache-Control"] = "no-store"
    return await route.dispatch(user.user_id, folder_id, "list", {"path": path})


@router.get("/folders/{folder_id}/file")
async def read_folder_file(folder_id: UUID, response: Response,
                           path: str = Query(min_length=1, max_length=4096),
                           user: CurrentUser = Depends(get_current_user), route=Depends(_route)):
    response.headers["Cache-Control"] = "no-store"
    return await route.dispatch(user.user_id, folder_id, "read", {"path": path})


@router.put("/folders/{folder_id}/file")
async def write_folder_file(folder_id: UUID, body: FileWrite,
                            user: CurrentUser = Depends(get_current_user), route=Depends(_route)):
    return await route.dispatch(user.user_id, folder_id, "write", body.model_dump())


@router.post("/folders/{folder_id}/files")
async def change_folder_files(folder_id: UUID, body: HostDirectoryOperation,
                              user: CurrentUser = Depends(get_current_user), route=Depends(_route)):
    if (body.action == "move") != (body.destination is not None):
        raise HTTPException(422, "A move needs a destination; other operations do not.")
    operation = body.model_dump(exclude={"action"}, exclude_none=True)
    return await route.dispatch(user.user_id, folder_id, body.action, operation)


@router.get("/folders/{folder_id}/events")
async def folder_events(folder_id: UUID, request: Request,
                        user: CurrentUser = Depends(get_current_user), route=Depends(_route)):
    stream = route.watch(user.user_id, folder_id)
    first = await anext(stream)  # Offline / denied answer as HTTP status, not as a stream.

    def frame(event):
        if event is None:
            return ": keepalive\n\n"
        if "unavailable" in event:
            return "event: unavailable\ndata: " + json.dumps(event["unavailable"]) + "\n\n"
        return "event: change\ndata: " + json.dumps(event) + "\n\n"

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


@router.post("/devices/{device_id}/challenge")
def challenge(device_id: UUID, user: CurrentUser = Depends(get_current_user),
              connection: HostConnectionAuth = Depends(get_connection_auth)):
    issued = connection.issue(user.user_id, device_id)
    if settings.HOST_GATEWAY_PUBLIC_URL:
        issued["connect_url"] = (settings.HOST_GATEWAY_PUBLIC_URL.rstrip("/") +
                                 f"/api/v1/hosts/devices/{device_id}/connect")
    return issued


@router.get("")
def catalog(response: Response, user: CurrentUser = Depends(get_current_user),
            service: HostService = Depends(get_host_service)):
    response.headers["Cache-Control"] = "no-store"
    return {"devices": service.catalog(user.user_id)}


@router.post("/devices", status_code=201)
def register_device(body: DeviceRegistration, user: CurrentUser = Depends(get_current_user),
                    service: HostService = Depends(get_host_service)):
    identity = service.register_device(user.user_id, body.public_key, body.name, body.platform)
    return {"device_id": identity}


@router.post("/devices/{device_id}/folders", status_code=201)
def register_folder(device_id: UUID, body: FolderRegistration,
                    user: CurrentUser = Depends(get_current_user),
                    service: HostService = Depends(get_host_service)):
    identity = service.register_folder(user.user_id, device_id, body.folder_id, body.name, body.writable)
    return {"folder_id": identity}


@router.patch("/devices/{device_id}/folders/{folder_id}")
def update_folder(device_id: UUID, folder_id: UUID, body: HostFolderUpdate,
                  user: CurrentUser = Depends(get_current_user),
                  service: HostService = Depends(get_host_service)):
    return service.update_folder(user.user_id, device_id, folder_id, body.name, body.writable,
                                 body.expected_revision)


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: UUID, user: CurrentUser = Depends(get_current_user),
                        service: HostService = Depends(get_host_service), route=Depends(_route)):
    revoked = bool(await run_blocking(service.revoke_device, user.user_id, device_id))
    if revoked:
        # New requests are already denied by the database; this closes the live
        # socket now, on the gateway and (via Redis) on every other instance.
        await route.revoked(device_id)
    return {"revoked": revoked}


@router.delete("/devices/{device_id}/folders/{folder_id}")
def remove_folder(device_id: UUID, folder_id: UUID, user: CurrentUser = Depends(get_current_user),
                  service: HostService = Depends(get_host_service)):
    # Admission rechecks the folder on every request and every watch keepalive.
    return {"removed": bool(service.remove_folder(user.user_id, device_id, folder_id))}
