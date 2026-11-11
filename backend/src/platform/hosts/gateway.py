"""Device sockets: one process owns every device socket it accepted.

Deployments run this inside the single-process ``device_gateway`` role; API
workers forward sockets to it over the internal network (see ``routing.py``).
In development/test the API process may host it directly. A database lease
that points at another gateway instance is reported offline, never served stale.

A device socket carries only byte tunnels (``tunnel.py``): each one an SSH
session that ends on the device, so files, commands and output are encrypted
end to end and never visible here. The Cloud admits each tunnel for a folder
and records that it opened and how it ended; nothing about what went through.
"""

import asyncio
import logging
import os
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from src.infra.execution.runner import run_blocking

from .connection_auth import HostConnectionAuth
from .tunnel import TunnelRelay, device_tunnel_frame, end_tunnels
from .repository import HostRepository

logger = logging.getLogger(__name__)

# The internal address when configured, so a future router can reach the owner.
GATEWAY_INSTANCE = os.environ.get("HOST_GATEWAY_INSTANCE_URL") or secrets.token_hex(16)
# Devices heartbeat every 10 seconds against a 30 second lease. A socket that
# stays silent past the lease (sleep, dead NAT) is closed instead of lingering.
HEARTBEAT_TIMEOUT = 35
LEASE_SECONDS = 30
CLOSE_REPLACED = 4001
CLOSE_INVALID = 4003
CLOSE_REVOKED = 4004
CLOSE_UNSUPPORTED = 4005
CLOSE_IDLE = 4008
# Host file protocol versions this gateway serves. A host speaking anything else
# is closed with 4005 before its challenge is consumed: it must update, not retry.
SUPPORTED_PROTOCOLS = frozenset({1})
PROOF_FIELDS = frozenset({"owner", "challenge_id", "epoch", "nonce", "signature",
                          "protocol", "boot_id", "app_version"})
APP_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}")


async def _close(websocket: WebSocket, code: int):
    with suppress(Exception):  # Already closed by the peer; closing is best-effort.
        await websocket.close(code=code)



@dataclass
class DeviceConnection:
    device: UUID
    epoch: UUID
    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tunnels: dict = field(default_factory=dict)
    closed: bool = False
    protocol: int = 1
    boot_id: UUID | None = None

    async def send(self, frame: dict):
        async with self.send_lock:
            await self.websocket.send_json(frame)



def parse_proof(proof) -> dict | None:
    """The validated handshake, or None for a protocol this gateway does not serve.

    The version is read before anything else so a newer or older host learns to
    update instead of failing as an invalid proof. Anything malformed raises.
    """
    if not isinstance(proof, dict):
        raise ValueError("Invalid proof")
    protocol = proof.get("protocol")
    if isinstance(protocol, bool) or not isinstance(protocol, int) or protocol not in SUPPORTED_PROTOCOLS:
        return None
    if set(proof) != PROOF_FIELDS or any(
            not isinstance(proof[name], str) or len(proof[name]) > 128 for name in PROOF_FIELDS - {"protocol"}):
        raise ValueError("Invalid proof")
    if not APP_VERSION.fullmatch(proof["app_version"]):
        raise ValueError("Invalid app version")
    return {"owner": str(UUID(proof["owner"])), "challenge": UUID(proof["challenge_id"]),
            "epoch": UUID(proof["epoch"]), "nonce": proof["nonce"], "signature": proof["signature"],
            "protocol": protocol, "boot_id": UUID(proof["boot_id"]), "app_version": proof["app_version"]}


class HostGateway:
    def __init__(self, repository=None, authentication=None):
        self.repository = repository or HostRepository()
        self.authentication = authentication or HostConnectionAuth(self.repository)
        self.connections: dict[UUID, DeviceConnection] = {}
        self.tunnels = TunnelRelay(self)

    async def connected(self, device: UUID, websocket: WebSocket):
        await websocket.accept()
        connection = None
        try:
            hello = parse_proof(await asyncio.wait_for(websocket.receive_json(), 10))
            if hello is None:
                await _close(websocket, CLOSE_UNSUPPORTED)
                return
            epoch = hello["epoch"]
            await run_blocking(self.authentication.accept, hello["owner"], device, hello["challenge"], epoch,
                               hello["nonce"], hello["signature"], GATEWAY_INSTANCE,
                               protocol=hello["protocol"], boot_id=hello["boot_id"],
                               app_version=hello["app_version"])
            connection = DeviceConnection(device, epoch, websocket, protocol=hello["protocol"],
                                          boot_id=hello["boot_id"])
            previous = self.connections.get(device)
            self.connections[device] = connection
            if previous:
                await _close(previous.websocket, CLOSE_REPLACED)
            await connection.send({"type": "ready", "epoch": str(epoch)})
            logger.info("host_device_connected", extra={
                "device": str(device), "protocol": hello["protocol"], "boot_id": str(hello["boot_id"]),
                "app_version": hello["app_version"]})
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), HEARTBEAT_TIMEOUT)
                except TimeoutError:
                    await _close(websocket, CLOSE_IDLE)
                    return
                if not isinstance(message, dict):
                    raise ValueError("Invalid device frame")
                kind = message.get("type")
                if kind == "heartbeat":
                    if set(message) != {"type"} or not await run_blocking(self.repository.command,
                            "host_connection_renew", p_device=str(device), p_epoch=str(epoch),
                            p_gateway=GATEWAY_INSTANCE, p_lease_seconds=LEASE_SECONDS):
                        raise ValueError("Device lease expired")
                    await connection.send({"type": "heartbeat_ack"})
                elif device_tunnel_frame(connection, message):
                    pass
                else:
                    raise ValueError("Invalid device frame")
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            await _close(websocket, CLOSE_INVALID)
        finally:
            if connection:
                connection.closed = True
                if self.connections.get(device) is connection:
                    del self.connections[device]
                end_tunnels(connection)
                logger.info("host_device_disconnected", extra={"device": str(device)})
                # Lease expiry still fences this connection; do not hide the primary failure.
                with suppress(Exception):
                    await run_blocking(self.repository.command, "host_connection_close", p_device=str(device),
                                       p_epoch=str(connection.epoch), p_gateway=GATEWAY_INSTANCE)

    async def disconnect(self, device: UUID):
        """Close this process's socket for a revoked device; new requests are already denied."""
        connection = self.connections.pop(device, None)
        if connection:
            end_tunnels(connection)
            await _close(connection.websocket, CLOSE_REVOKED)

    async def _admit(self, owner: str, folder: UUID, action: str):
        try:
            return await run_blocking(self.repository.command, "host_folder_admit",
                                      p_actor=owner, p_folder=str(folder), p_operation=action)
        except Exception as exc:
            if getattr(exc, "code", "") == "55000":
                raise HTTPException(503, {"code": "device_offline"}) from exc
            raise HTTPException(403, {"code": "folder_unavailable"}) from exc

    def _connection(self, admitted: dict) -> DeviceConnection:
        connection = self.connections.get(UUID(admitted["device_id"]))
        if (admitted["gateway_instance"] != GATEWAY_INSTANCE or connection is None or
                connection.closed or str(connection.epoch) != admitted["epoch"]):
            raise HTTPException(503, {"code": "device_offline"})
        return connection

    async def _audit(self, owner: str, device: UUID, folder: UUID, action: str, outcome: str):
        """Connection metadata only: who opened a tunnel to which folder, and how it ended."""
        try:
            await run_blocking(self.repository.command, "host_file_event_record",
                               p_actor=owner, p_device=str(device), p_folder=str(folder),
                               p_operation=action, p_outcome=outcome)
        except Exception as exc:
            raise HTTPException(503, {"code": "audit_unavailable"}) from exc
