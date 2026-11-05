"""``SERVICE_ROLE=device_gateway``: one process that owns every device socket.

Public: the device WebSocket (signed, owner-bound proof). Internal: dispatch,
watch and revocation for API workers, authenticated by a shared secret that
never leaves the private network. Run with exactly one worker.
"""

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.config import settings
from src.platform.project.write_lease import install_storage_write_admission

from .execution import serve_exec
from .tunnel import serve_tunnel
from .gateway import FILE_ACTIONS, HostGateway
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


class Dispatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: UUID
    folder_id: UUID
    action: str = Field(max_length=16)
    operation: dict


@app.get("/health")
def health():
    return {"status": "ok", "role": "device_gateway", "devices": len(gateway().connections)}


@app.websocket("/api/v1/hosts/devices/{device_id}/connect")
async def connect_device(websocket: WebSocket, device_id: UUID):
    if not settings.HOST_DEVICES_ENABLED:
        await websocket.close(code=4003)
        return
    await gateway().connected(device_id, websocket)


@app.websocket("/api/v1/hosts/folders/{folder_id}/exec")
async def folder_exec(websocket: WebSocket, folder_id: UUID):
    """A client terminal attaches here, where the device socket lives."""
    if not settings.HOST_DEVICES_ENABLED:
        await websocket.close(code=4003)
        return
    from src.platform.auth.dependencies import get_auth_service

    await serve_exec(gateway(), websocket, folder_id, get_auth_service().get_current_user)


@app.websocket("/api/v1/hosts/folders/{folder_id}/tunnel")
async def folder_tunnel(websocket: WebSocket, folder_id: UUID):
    """A client's SSH tunnel attaches here, where the device socket lives; bytes only."""
    if not settings.HOST_DEVICES_ENABLED:
        await websocket.close(code=4003)
        return
    from src.platform.auth.dependencies import get_auth_service

    await serve_tunnel(gateway(), websocket, folder_id, get_auth_service().get_current_user)


@app.post("/internal/hosts/dispatch", dependencies=[Depends(internal)])
async def dispatch(body: Dispatch):
    if body.action not in FILE_ACTIONS:
        raise HTTPException(400, {"code": "file_operation_failed"})
    return await gateway().dispatch(str(body.owner), body.folder_id, body.action, body.operation)


@app.get("/internal/hosts/events", dependencies=[Depends(internal)])
async def events(owner: UUID = Query(), folder_id: UUID = Query()):
    stream = gateway().watch(str(owner), folder_id)
    first = await anext(stream)  # Admission errors surface as HTTP status, before streaming.

    async def lines():
        try:
            yield json.dumps(first if first is not None else {"keepalive": True}) + "\n"
            async for event in stream:
                yield json.dumps(event if event is not None else {"keepalive": True}) + "\n"
        finally:
            await stream.aclose()

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store"})


@app.post("/internal/hosts/devices/{device_id}/disconnect", dependencies=[Depends(internal)])
async def disconnect(device_id: UUID):
    await gateway().disconnect(device_id)
    return {"disconnected": True}
