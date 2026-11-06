"""Authenticated human API for the owner's devices and registered folders.

The Cloud keeps the inventory (devices, folders, capabilities) and admits each
connection; folder content is reached only inside an SSH session that ends on
the device, through a byte tunnel (``tunnel.py``): the client gets a short
certificate here and the host verifies it. Device sockets are served by the
``device_gateway`` role (see ``routing.py``), or in-process in development.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket
from pydantic import BaseModel, ConfigDict, Field

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser

from .connection_auth import HostConnectionAuth
from .gateway import HostGateway
from .routing import ForwardingRoute, LocalRoute, forward_websocket
from .service import HostService
from .socket_auth import is_person
from .ssh_authority import device_host_key, host_ssh_authority
from .tunnel import serve_tunnel


def require_host_devices_enabled():
    if not settings.HOST_DEVICES_ENABLED:
        raise HTTPException(404, "Not found")


def get_person(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Hosts serve a person's own sign-in only: never an anonymous session, and never a
    runtime credential (Agent, Sandbox, MCP, CLI) or PuppyOne's own cloud Agents."""
    if not is_person(user):
        raise HTTPException(403, {"code": "person_required"})
    return user


router = APIRouter(prefix="/api/v1/hosts", tags=["hosts"],
                   dependencies=[Depends(require_host_devices_enabled)])


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_key: str = Field(min_length=40, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    platform: Literal["darwin", "win32", "linux"]


class DeviceChallengeRequest(BaseModel):
    """Signed by the device key over owner, device and time; no human JWT."""
    model_config = ConfigDict(extra="forbid")
    owner: UUID
    timestamp: int = Field(ge=0, le=2**40)
    signature: str = Field(min_length=86, max_length=128)


class HostSshCertificateRequest(BaseModel):
    """The client's own SSH key; the certificate lets it log in to this device's host service."""
    model_config = ConfigDict(extra="forbid")
    public_key: str = Field(min_length=60, max_length=200)


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
    # Running things in the folder; each also needs writable and the host's own consent.
    terminal: bool = False
    git: bool = False
    agent: bool = False


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


def get_file_route(gateway: HostGateway = Depends(get_gateway)) -> LocalRoute | ForwardingRoute:
    return get_route() if forwarding() else LocalRoute(gateway)


@router.websocket("/devices/{device_id}/connect")
async def connect_device(websocket: WebSocket, device_id: UUID):
    # This path has no human JWT: the owner-bound, one-use challenge and
    # signature are verified before any file request can be delivered. When a
    # device_gateway is deployed, API workers only carry the socket to it.
    if not settings.HOST_DEVICES_ENABLED:
        await websocket.close(code=4003)
        return
    if forwarding():
        await forward_websocket(websocket, f"/api/v1/hosts/devices/{device_id}/connect")
        return
    await get_gateway().connected(device_id, websocket)


@router.websocket("/folders/{folder_id}/tunnel")
async def folder_tunnel(websocket: WebSocket, folder_id: UUID):
    # Bytes only: the SSH session inside ends on the host, encrypted end to end.
    if not settings.HOST_DEVICES_ENABLED:
        await websocket.close(code=4003)
        return
    if forwarding():
        await forward_websocket(websocket, f"/api/v1/hosts/folders/{folder_id}/tunnel")
        return
    from src.platform.auth.dependencies import get_auth_service

    await serve_tunnel(get_gateway(), websocket, folder_id, get_auth_service().get_current_user)


@router.get("/ssh-authority")
def ssh_authority():
    """The key hosts trust for SSH user certificates. Public by nature."""
    return {"keys": [host_ssh_authority().public_key()]}


@router.post("/devices/{device_id}/ssh-certificate")
def ssh_certificate(device_id: UUID, body: HostSshCertificateRequest,
                    user: CurrentUser = Depends(get_person),
                    connection: HostConnectionAuth = Depends(get_connection_auth)):
    """A five-minute certificate for one of the owner's own active devices, and its host key."""
    try:
        record = connection.repository.command("host_device_key", p_owner=user.user_id, p_device=str(device_id))
    except Exception as exc:
        if getattr(exc, "code", "") == "42501":
            raise HTTPException(404, {"code": "device_unavailable"}) from exc
        raise HTTPException(503, {"code": "device_unavailable"}) from exc
    issued = host_ssh_authority().issue(user.user_id, device_id, body.public_key)
    return {**issued, "host_key": device_host_key(record["public_key"]),
            "gateway_url": settings.HOST_GATEWAY_PUBLIC_URL or None}


@router.post("/devices/{device_id}/device-challenge")
def device_challenge(device_id: UUID, body: DeviceChallengeRequest,
                     connection: HostConnectionAuth = Depends(get_connection_auth)):
    # A host keeps sharing with its own key while nobody is signed in on it. The
    # signature is checked against the registered key before anything is issued.
    issued = connection.issue_for_device(str(body.owner), device_id, body.timestamp, body.signature)
    return with_connect_url(issued, device_id)


def with_connect_url(issued: dict, device_id: UUID) -> dict:
    if settings.HOST_GATEWAY_PUBLIC_URL:
        issued["connect_url"] = (settings.HOST_GATEWAY_PUBLIC_URL.rstrip("/") +
                                 f"/api/v1/hosts/devices/{device_id}/connect")
    return issued


@router.get("")
def catalog(response: Response, user: CurrentUser = Depends(get_person),
            service: HostService = Depends(get_host_service)):
    response.headers["Cache-Control"] = "no-store"
    # Where clients attach terminals: the device gateway when one is deployed.
    return {"devices": service.catalog(user.user_id),
            "gateway_url": settings.HOST_GATEWAY_PUBLIC_URL.rstrip("/") or None}


@router.post("/devices", status_code=201)
def register_device(body: DeviceRegistration, user: CurrentUser = Depends(get_person),
                    service: HostService = Depends(get_host_service)):
    identity = service.register_device(user.user_id, body.public_key, body.name, body.platform)
    return {"device_id": identity}


@router.post("/devices/{device_id}/folders", status_code=201)
def register_folder(device_id: UUID, body: FolderRegistration,
                    user: CurrentUser = Depends(get_person),
                    service: HostService = Depends(get_host_service)):
    identity = service.register_folder(user.user_id, device_id, body.folder_id, body.name, body.writable)
    return {"folder_id": identity}


@router.patch("/devices/{device_id}/folders/{folder_id}")
def update_folder(device_id: UUID, folder_id: UUID, body: HostFolderUpdate,
                  user: CurrentUser = Depends(get_person),
                  service: HostService = Depends(get_host_service)):
    return service.update_folder(user.user_id, device_id, folder_id, body.name, body.writable,
                                 body.expected_revision, terminal=body.terminal, git=body.git, agent=body.agent)


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: UUID, user: CurrentUser = Depends(get_person),
                        service: HostService = Depends(get_host_service), route=Depends(get_file_route)):
    revoked = bool(await run_blocking(service.revoke_device, user.user_id, device_id))
    if revoked:
        # New requests are already denied by the database; this closes the live
        # socket now, on the gateway and (via Redis) on every other instance.
        await route.revoked(device_id)
    return {"revoked": revoked}


@router.delete("/devices/{device_id}/folders/{folder_id}")
def remove_folder(device_id: UUID, folder_id: UUID, user: CurrentUser = Depends(get_person),
                  service: HostService = Depends(get_host_service)):
    # Admission rechecks the folder on every request and every watch keepalive.
    return {"removed": bool(service.remove_folder(user.user_id, device_id, folder_id))}
