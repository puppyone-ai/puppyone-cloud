"""Single-process, fail-closed device relay prototype. Not a multi-replica gateway.

No route may enable this without an explicit deployment/acceptance gate. A missing
local socket is offline, even if a database lease points at another API instance.
"""

import asyncio
import secrets
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from src.infra.execution.runner import run_blocking

from .connection_auth import HostConnectionAuth
from .repository import HostRepository

GATEWAY_INSTANCE = secrets.token_hex(16)
MAX_PENDING = 64
MAX_RESPONSE_BYTES = 12 * 1024 * 1024


@dataclass
class DeviceConnection:
    device: UUID
    epoch: UUID
    websocket: WebSocket
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class HostGateway:
    def __init__(self, repository=None, authentication=None):
        self.repository = repository or HostRepository()
        self.authentication = authentication or HostConnectionAuth(self.repository)
        self.connections: dict[UUID, DeviceConnection] = {}

    async def connected(self, device: UUID, websocket: WebSocket):
        await websocket.accept()
        connection = None
        try:
            proof = await asyncio.wait_for(websocket.receive_json(), 10)
            if not isinstance(proof, dict) or set(proof) != {
                "owner", "challenge_id", "epoch", "nonce", "signature"
            } or any(not isinstance(value, str) or len(value) > 128 for value in proof.values()):
                raise ValueError("Invalid proof")
            owner = str(UUID(proof["owner"]))
            challenge = UUID(proof["challenge_id"])
            epoch = UUID(proof["epoch"])
            await run_blocking(self.authentication.accept, owner, device, challenge, epoch,
                               proof["nonce"], proof["signature"], GATEWAY_INSTANCE)
            connection = DeviceConnection(device, epoch, websocket)
            previous = self.connections.get(device)
            self.connections[device] = connection
            if previous:
                await previous.websocket.close(code=4001)
            async with connection.send_lock:
                await websocket.send_json({"type": "ready", "epoch": str(epoch)})
            while True:
                message = await websocket.receive_json()
                if isinstance(message, dict) and message.get("type") == "heartbeat":
                    if set(message) != {"type"} or not await run_blocking(self.repository.command,
                            "host_connection_renew", p_device=str(device), p_epoch=str(epoch),
                            p_gateway=GATEWAY_INSTANCE, p_lease_seconds=30):
                        raise ValueError("Device lease expired")
                    async with connection.send_lock:
                        await websocket.send_json({"type": "heartbeat_ack"})
                    continue
                if not isinstance(message, dict) or message.get("type") != "reply" or not isinstance(message.get("id"), str):
                    raise ValueError("Invalid response frame")
                request_id = message["id"]
                future = connection.pending.pop(request_id, None)
                if future and not future.done():
                    if len(str(message)) > MAX_RESPONSE_BYTES:
                        future.set_exception(HTTPException(502, "Device response too large"))
                    else:
                        future.set_result(message)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            await websocket.close(code=4003)
        finally:
            if connection:
                if self.connections.get(device) is connection:
                    del self.connections[device]
                for future in connection.pending.values():
                    if not future.done():
                        future.set_exception(HTTPException(503, {"code": "device_disconnected", "outcome_unknown": True}))
                try:
                    await run_blocking(self.repository.command, "host_connection_close", p_device=str(device),
                                       p_epoch=str(connection.epoch), p_gateway=GATEWAY_INSTANCE)
                except Exception:
                    pass  # Lease expiry still fences this connection; do not hide the primary failure.

    async def dispatch(self, owner: str, folder: UUID, action: str, operation: dict):
        try:
            admitted = await run_blocking(self.repository.command, "host_folder_admit",
                                          p_actor=owner, p_folder=str(folder), p_operation=action)
        except Exception as exc:
            raise HTTPException(403, "Host folder unavailable") from exc
        device = UUID(admitted["device_id"])
        connection = self.connections.get(device)
        if (admitted["gateway_instance"] != GATEWAY_INSTANCE or connection is None or
            str(connection.epoch) != admitted["epoch"]):
            raise HTTPException(503, {"code": "device_offline"})
        if len(connection.pending) >= MAX_PENDING:
            raise HTTPException(503, {"code": "device_busy"})
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        connection.pending[request_id] = future
        try:
            async with connection.send_lock:
                await connection.websocket.send_json({"type": "file", **operation, "id": request_id,
                    "folder_id": str(folder), "action": action})
            reply = await asyncio.wait_for(future, 25)
            if reply.get("error"):
                raise HTTPException(409 if reply["error"] == "conflict" else 400, {"code": reply["error"]})
            return reply.get("result")
        except TimeoutError as exc:
            raise HTTPException(504, {"code": "device_timeout", "outcome_unknown": action not in ("read", "list")}) from exc
        finally:
            connection.pending.pop(request_id, None)
