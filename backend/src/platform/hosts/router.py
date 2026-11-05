"""Authenticated human inventory API. No file or device transport is exposed here."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser

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
    name: str = Field(min_length=1, max_length=120)
    writable: bool = False


def get_host_service() -> HostService:
    return HostService()


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
    identity = service.register_folder(user.user_id, device_id, body.name, body.writable)
    return {"folder_id": identity}


@router.delete("/devices/{device_id}")
def revoke_device(device_id: UUID, user: CurrentUser = Depends(get_current_user),
                        service: HostService = Depends(get_host_service)):
    return {"revoked": bool(service.revoke_device(user.user_id, device_id))}


@router.delete("/devices/{device_id}/folders/{folder_id}")
def remove_folder(device_id: UUID, folder_id: UUID, user: CurrentUser = Depends(get_current_user),
                        service: HostService = Depends(get_host_service)):
    return {"removed": bool(service.remove_folder(user.user_id, device_id, folder_id))}
