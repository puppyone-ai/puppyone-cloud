"""Byte tunnels from the owner's client to a folder's host, for SSH that ends on the host.

The gateway admits a tunnel like any folder request (the owner, the folder, the
current device connection) and then only moves bytes. The SSH session inside
is encrypted end to end between the client and the host service, so this
process never sees file content, commands or output; it records only that a
tunnel opened and how it ended.

Client side: a WebSocket on ``/api/v1/hosts/folders/{folder}/tunnel`` with a
bearer JWT. After ``{"type": "ready"}`` every binary message is tunnel bytes.
Device side: ``tunnel_open`` (with the folder, which the host requires as the
SSH user name), ``tunnel_data`` (base64) and ``tunnel_close`` frames on the
device socket. A tunnel that falls behind is closed; the device socket never is.
"""

import asyncio
import base64
import binascii
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from src.infra.execution.runner import run_blocking

from .execution import token_from

logger = logging.getLogger(__name__)

MAX_TUNNELS_PER_DEVICE = 16
MAX_CHUNK = 64 * 1024
# Bytes waiting for a slow client before its tunnel is closed. SSH's own
# window keeps far less than this in flight.
MAX_QUEUED_BYTES = 8 * 1024 * 1024
OPEN_TIMEOUT = 15
TUNNEL_REFUSALS = frozenset({"folder_unavailable", "not_allowed", "tunnel_limit", "tunnel_unavailable"})


@dataclass
class Tunnel:
    id: str
    folder: UUID
    opened: asyncio.Future
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    queued: int = 0
    closed: bool = False
    # The host ended it (closed or refused): nothing to tell the host back.
    ended_by_device: bool = False

    def deliver(self, data: bytes | None):
        """From the device; None ends the tunnel."""
        if self.closed:
            return
        if data is None:
            self.closed = True
            self.queue.put_nowait(None)
            return
        self.queued += len(data)
        if self.queued > MAX_QUEUED_BYTES:
            # A client that cannot keep up loses its tunnel, never the device socket.
            self.closed = True
            self.queue.put_nowait(None)
            return
        self.queue.put_nowait(data)


def device_tunnel_frame(connection, message: dict) -> bool:
    """Route one tunnel frame from the device. False if it is not a tunnel frame."""
    kind = message.get("type")
    if kind not in {"tunnel_opened", "tunnel_refused", "tunnel_data", "tunnel_close"}:
        return False
    tunnel = connection.tunnels.get(message.get("stream")) if isinstance(message.get("stream"), str) else None
    if tunnel is None:
        return True  # A late frame for a tunnel that already ended.
    if kind == "tunnel_opened":
        if not tunnel.opened.done():
            tunnel.opened.set_result(None)
    elif kind == "tunnel_refused":
        tunnel.ended_by_device = True
        code = message.get("error") if message.get("error") in TUNNEL_REFUSALS else "tunnel_unavailable"
        if not tunnel.opened.done():
            tunnel.opened.set_result(code)
        tunnel.deliver(None)
    elif kind == "tunnel_data":
        data = message.get("data")
        try:
            raw = base64.b64decode(data, validate=True) if isinstance(data, str) and len(data) <= 2 * MAX_CHUNK else None
        except (ValueError, binascii.Error):
            raw = None
        if raw is None:
            raise ValueError("Invalid tunnel frame")
        tunnel.deliver(raw)
    else:
        tunnel.ended_by_device = True
        if not tunnel.opened.done():
            tunnel.opened.set_result("tunnel_unavailable")
        tunnel.deliver(None)
    return True


def end_tunnels(connection):
    """The device went away: every tunnel ends now."""
    for tunnel in list(connection.tunnels.values()):
        if not tunnel.opened.done():
            tunnel.opened.set_result("tunnel_unavailable")
        tunnel.deliver(None)
    connection.tunnels.clear()


class TunnelRelay:
    def __init__(self, gateway):
        self.gateway = gateway

    async def run(self, owner: str, folder: UUID, websocket: WebSocket):
        try:
            admitted = await self.gateway._admit(owner, folder, "read")
            connection = self.gateway._connection(admitted)
        except HTTPException as refused:
            code = refused.detail.get("code") if isinstance(refused.detail, dict) else "tunnel_unavailable"
            await websocket.send_json({"type": "error", "code": code})
            return
        device = connection.device
        if len(connection.tunnels) >= MAX_TUNNELS_PER_DEVICE:
            await websocket.send_json({"type": "error", "code": "tunnel_limit"})
            return
        tunnel = Tunnel(str(uuid4()), folder, asyncio.get_running_loop().create_future())
        connection.tunnels[tunnel.id] = tunnel
        outcome = "failed"
        try:
            await self.gateway._audit(owner, device, folder, "tunnel", "intended")
            await connection.send({"type": "tunnel_open", "stream": tunnel.id, "folder_id": str(folder)})
            refused = await asyncio.wait_for(tunnel.opened, OPEN_TIMEOUT)
            if refused:
                await websocket.send_json({"type": "error", "code": refused})
                return
            await websocket.send_json({"type": "ready"})
            logger.info("host_tunnel_opened", extra={"device": str(device)})
            outcome = await self._pump(connection, tunnel, websocket)
        except (TimeoutError, asyncio.TimeoutError):
            with suppress(Exception):
                await websocket.send_json({"type": "error", "code": "tunnel_unavailable"})
        finally:
            connection.tunnels.pop(tunnel.id, None)

            async def finish():
                if not connection.closed and not tunnel.ended_by_device:
                    with suppress(Exception):
                        await connection.send({"type": "tunnel_close", "stream": tunnel.id})
                with suppress(Exception):
                    await self.gateway._audit(owner, device, folder, "tunnel", outcome)
                logger.info("host_tunnel_closed", extra={"device": str(device), "outcome": outcome})

            # The host must learn the tunnel ended even if this handler is being cancelled.
            with suppress(asyncio.CancelledError):
                await asyncio.shield(asyncio.ensure_future(finish()))

    async def _pump(self, connection, tunnel: Tunnel, websocket: WebSocket) -> str:
        async def to_device():
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                data = message.get("bytes")
                if data is None:
                    raise ValueError("Tunnel bytes must be binary")
                for start in range(0, len(data), MAX_CHUNK):
                    if connection.closed:
                        return
                    await connection.send({"type": "tunnel_data", "stream": tunnel.id,
                                           "data": base64.b64encode(data[start:start + MAX_CHUNK]).decode("ascii")})

        async def to_client():
            while True:
                data = await tunnel.queue.get()
                if data is None:
                    return
                tunnel.queued -= len(data)
                await websocket.send_bytes(data)

        tasks = [asyncio.create_task(to_device()), asyncio.create_task(to_client())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            failed = any(task.exception() for task in done if not task.cancelled())
            return "failed" if failed else "succeeded"
        finally:
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task


async def serve_tunnel(gateway, websocket: WebSocket, folder: UUID, authenticate):
    """One client tunnel: authenticate, admit, relay bytes until either side ends."""
    token, subprotocol = token_from(websocket)
    try:
        user = await run_blocking(authenticate, token) if token else None
    except Exception:
        user = None
    if user is None:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol=subprotocol)
    try:
        await gateway.tunnels.run(str(user.user_id), folder, websocket)
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        with suppress(Exception):
            await websocket.close()
