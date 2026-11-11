"""``SERVICE_ROLE=device_gateway``: one process that owns every device socket.

Public: the device WebSocket (signed, owner-bound proof) and clients' byte
tunnels to it. Internal: revocation for API workers, authenticated by a shared
secret that never leaves the private network. Run with exactly one worker.
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket

from src.config import settings
from src.platform.project.write_lease import install_storage_write_admission

from .tunnel import serve_tunnel
from .gateway import HostGateway
from .routing import SECRET_HEADER, gateway_secret_matches, listen_for_revocations

# It never writes Project storage, but like every deployed process it runs under
# the storage write admission, so a future write cannot bypass a Project lease.
install_storage_write_admission()

_gateway: HostGateway | None = None


def gateway() -> HostGateway:
    global _gateway
    if _gateway is None:
        _gateway = HostGateway()
    return _gateway


def internal(secret: str | None = Header(default=None, alias=SECRET_HEADER)):
    if not gateway_secret_matches(secret):
        raise HTTPException(404, "Not found")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    listener = asyncio.create_task(listen_for_revocations(gateway()))
    try:
        yield
    finally:
        listener.cancel()
        with suppress(asyncio.CancelledError):
            await listener


app = FastAPI(title="PuppyOne device gateway", docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "role": "device_gateway", "devices": len(gateway().connections)}


@app.websocket("/api/v1/hosts/devices/{device_id}/connect")
async def connect_device(websocket: WebSocket, device_id: UUID):
    if not settings.HOST_DEVICES_ENABLED:
        await websocket.close(code=4003)
        return
    await gateway().connected(device_id, websocket)


@app.websocket("/api/v1/hosts/folders/{folder_id}/tunnel")
async def folder_tunnel(websocket: WebSocket, folder_id: UUID):
    """A client's SSH tunnel attaches here, where the device socket lives; bytes only."""
    if not settings.HOST_DEVICES_ENABLED:
        await websocket.close(code=4003)
        return
    from src.platform.auth.dependencies import get_auth_service

    await serve_tunnel(gateway(), websocket, folder_id, get_auth_service().get_current_user)


@app.post("/internal/hosts/devices/{device_id}/disconnect", dependencies=[Depends(internal)])
async def disconnect(device_id: UUID):
    await gateway().disconnect(device_id)
    return {"disconnected": True}
