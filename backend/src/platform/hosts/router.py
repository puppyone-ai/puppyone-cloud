"""Authenticated human inventory API. No file or device transport is exposed here."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket
from pydantic import BaseModel, ConfigDict, Field

from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser

from .connection_auth import HostConnectionAuth
from .gateway import HostGateway
from .service import HostService

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


class FileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    content_base64: str = Field(max_length=12 * 1024 * 1024)
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


def get_host_service() -> HostService:
    return HostService()


def get_connection_auth() -> HostConnectionAuth:
    return HostConnectionAuth()


_gateway: HostGateway | None = None


def get_gateway() -> HostGateway:
    global _gateway
    if _gateway is None:
        _gateway = HostGateway()
    return _gateway


@router.websocket("/devices/{device_id}/connect")
async def connect_device(websocket: WebSocket, device_id: UUID):
    # This path has no human JWT: the owner-bound, one-use challenge and
    # signature are verified before any file request can be delivered.
    if not settings.HOST_DEVICES_ENABLED:
        await websocket.close(code=4003)
        return
    await get_gateway().connected(device_id, websocket)


@router.get("/folders/{folder_id}/files")
async def list_folder(folder_id: UUID, response: Response,
                      path: str = Query(default="", max_length=4096),
                      user: CurrentUser = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return await get_gateway().dispatch(user.user_id, folder_id, "list", {"path": path})


@router.get("/folders/{folder_id}/file")
async def read_folder_file(folder_id: UUID, response: Response,
                           path: str = Query(min_length=1, max_length=4096),
                           user: CurrentUser = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return await get_gateway().dispatch(user.user_id, folder_id, "read", {"path": path})


@router.put("/folders/{folder_id}/file")
async def write_folder_file(folder_id: UUID, body: FileWrite,
                            user: CurrentUser = Depends(get_current_user)):
    return await get_gateway().dispatch(user.user_id, folder_id, "write", body.model_dump())


@router.post("/devices/{device_id}/challenge")
def challenge(device_id: UUID, user: CurrentUser = Depends(get_current_user),
              connection: HostConnectionAuth = Depends(get_connection_auth)):
    return connection.issue(user.user_id, device_id)


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


@router.delete("/devices/{device_id}")
def revoke_device(device_id: UUID, user: CurrentUser = Depends(get_current_user),
                        service: HostService = Depends(get_host_service)):
    return {"revoked": bool(service.revoke_device(user.user_id, device_id))}


@router.delete("/devices/{device_id}/folders/{folder_id}")
def remove_folder(device_id: UUID, folder_id: UUID, user: CurrentUser = Depends(get_current_user),
                        service: HostService = Depends(get_host_service)):
    return {"removed": bool(service.remove_folder(user.user_id, device_id, folder_id))}
