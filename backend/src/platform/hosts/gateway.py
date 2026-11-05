"""Fail-closed device relay: one process owns every device socket it accepted.

Deployments run this inside the single-process ``device_gateway`` role; API
workers forward to it over the internal network (see ``routing.py``). In
development/test the API process may host it directly. A database lease that
points at another gateway instance is reported offline, never served stale.
The relay records operation metadata only: no paths, file content or keys.
"""

import asyncio
import logging
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from src.infra.execution.runner import run_blocking

from .connection_auth import HostConnectionAuth
from .repository import HostRepository

logger = logging.getLogger(__name__)

# The internal address when configured, so a future router can reach the owner.
GATEWAY_INSTANCE = os.environ.get("HOST_GATEWAY_INSTANCE_URL") or secrets.token_hex(16)
MAX_PENDING = 64
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
REQUEST_TIMEOUT = 25
# Devices heartbeat every 10 seconds against a 30 second lease. A socket that
# stays silent past the lease (sleep, dead NAT) is closed instead of lingering.
HEARTBEAT_TIMEOUT = 35
LEASE_SECONDS = 30
# A watcher re-checks its authority this often and keeps proxies from idling out.
KEEPALIVE_SECONDS = 15
SUBSCRIBER_QUEUE = 64
MAX_EVENT_PATHS = 256
MAX_WATCHES = 64
CLOSE_REPLACED = 4001
CLOSE_INVALID = 4003
CLOSE_REVOKED = 4004
CLOSE_IDLE = 4008
FILE_ACTIONS = frozenset({"list", "read", "write", "mkdir", "move", "remove"})
MUTATIONS = frozenset({"write", "mkdir", "move", "remove"})
# Device error codes the HTTP caller may see; anything else is a generic failure.
DEVICE_ERRORS = {"conflict": 409, "already_exists": 409, "not_empty": 409, "not_found": 404,
                 "folder_unavailable": 403, "root_changed": 403, "invalid_path": 400,
                 "unsupported_file": 400, "too_many_entries": 400, "invalid_content": 400,
                 "revision_required": 400, "file_operation_failed": 400}
_CLOSED = object()


async def _close(websocket: WebSocket, code: int):
    with suppress(Exception):  # Already closed by the peer; closing is best-effort.
        await websocket.close(code=code)


def _elapsed(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


class Subscriber:
    """One SSE reader. A slow reader loses backlog, never blocks the device socket."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)

    def offer(self, event):
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(_CLOSED if event is _CLOSED else {"paths": [], "reset": True})


@dataclass
class FolderWatch:
    folder: UUID
    subscription: str
    subscribers: set = field(default_factory=set)


@dataclass
class DeviceConnection:
    device: UUID
    epoch: UUID
    websocket: WebSocket
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    watches: dict[UUID, FolderWatch] = field(default_factory=dict)
    closed: bool = False

    async def send(self, frame: dict):
        async with self.send_lock:
            await self.websocket.send_json(frame)

    def watch_for(self, subscription) -> FolderWatch | None:
        return next((item for item in self.watches.values() if item.subscription == subscription), None)


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
                await _close(previous.websocket, CLOSE_REPLACED)
            await connection.send({"type": "ready", "epoch": str(epoch)})
            logger.info("host_device_connected", extra={"device": str(device)})
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
                elif kind == "reply" and isinstance(message.get("id"), str):
                    future = connection.pending.pop(message["id"], None)
                    if future and not future.done():
                        if len(str(message)) > MAX_RESPONSE_BYTES:
                            future.set_exception(HTTPException(502, {"code": "file_operation_failed"}))
                        else:
                            future.set_result(message)
                elif kind == "event" and isinstance(message.get("subscription"), str):
                    self._fan_out(connection, message)
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
                for future in connection.pending.values():
                    if not future.done():
                        future.set_exception(HTTPException(503, {"code": "device_disconnected",
                                                                 "outcome_unknown": True}))
                for watch in connection.watches.values():
                    for subscriber in watch.subscribers:
                        subscriber.offer(_CLOSED)
                connection.watches.clear()
                logger.info("host_device_disconnected", extra={"device": str(device)})
                # Lease expiry still fences this connection; do not hide the primary failure.
                with suppress(Exception):
                    await run_blocking(self.repository.command, "host_connection_close", p_device=str(device),
                                       p_epoch=str(connection.epoch), p_gateway=GATEWAY_INSTANCE)

    @staticmethod
    def _fan_out(connection: DeviceConnection, message: dict):
        watch = connection.watch_for(message["subscription"])
        if watch is None:
            return  # A late event for a watch that already ended.
        paths = message.get("paths")
        reset = message.get("reset") is True or not isinstance(paths, list) or len(paths) > MAX_EVENT_PATHS
        if not reset and any(not isinstance(item, str) or len(item) > 4096 for item in paths):
            reset = True
        event = {"paths": [], "reset": True} if reset else {"paths": paths, "reset": False}
        for subscriber in watch.subscribers:
            subscriber.offer(event)

    async def disconnect(self, device: UUID):
        """Close this process's socket for a revoked device; new requests are already denied."""
        connection = self.connections.pop(device, None)
        if connection:
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

    async def _audit(self, owner: str, device: UUID, folder: UUID, action: str, outcome: str,
                     *, sent: bool = False):
        try:
            await run_blocking(self.repository.command, "host_file_event_record",
                               p_actor=owner, p_device=str(device), p_folder=str(folder),
                               p_operation=action, p_outcome=outcome)
        except Exception as exc:
            raise HTTPException(503, {"code": "audit_unavailable",
                                      "outcome_unknown": sent and action in MUTATIONS}) from exc

    async def dispatch(self, owner: str, folder: UUID, action: str, operation: dict):
        if action not in FILE_ACTIONS:
            raise HTTPException(400, {"code": "file_operation_failed"})
        started = time.monotonic()
        admitted = await self._admit(owner, folder, action)
        admit_ms = _elapsed(started)
        connection = self._connection(admitted)
        device = connection.device
        if len(connection.pending) >= MAX_PENDING:
            raise HTTPException(503, {"code": "device_busy"})
        mutating = action in MUTATIONS
        outcome = "failed"
        round_trip = time.monotonic()
        # Audit failure before delivery denies the operation. A failure after a
        # mutation is outcome-unknown; the client must keep its draft, not replay.
        await self._audit(owner, device, folder, action, "intended")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        connection.pending[request_id] = future
        try:
            try:
                await connection.send({"type": "file", **operation, "id": request_id,
                                       "folder_id": str(folder), "action": action})
                round_trip = time.monotonic()
                reply = await asyncio.wait_for(future, REQUEST_TIMEOUT)
            except TimeoutError as exc:
                outcome = "unknown" if mutating else "failed"
                await self._audit(owner, device, folder, action, outcome, sent=True)
                raise HTTPException(504, {"code": "device_timeout", "outcome_unknown": mutating}) from exc
            except Exception:
                outcome = "unknown" if mutating else "failed"
                await self._audit(owner, device, folder, action, outcome, sent=True)
                raise
            if reply.get("error"):
                await self._audit(owner, device, folder, action, "failed", sent=True)
                error = reply["error"]
                code = error if isinstance(error, str) and error in DEVICE_ERRORS else "file_operation_failed"
                raise HTTPException(DEVICE_ERRORS[code], {"code": code})
            await self._audit(owner, device, folder, action, "succeeded", sent=True)
            outcome = "succeeded"
            result = reply.get("result")
            return result if isinstance(result, dict) else {}
        finally:
            connection.pending.pop(request_id, None)
            # Phase timings only; never a path, a name or file content.
            logger.info("host_file_operation", extra={
                "action": action, "outcome": outcome, "admit_ms": admit_ms,
                "device_ms": _elapsed(round_trip), "total_ms": _elapsed(started)})

    async def watch(self, owner: str, folder: UUID) -> AsyncIterator[dict | None]:
        """Yield change events; ``None`` is a keepalive. Ends with an ``unavailable`` event."""
        admitted = await self._admit(owner, folder, "watch")
        connection = self._connection(admitted)
        device = connection.device
        watch = connection.watches.get(folder)
        if watch is None:
            if len(connection.watches) >= MAX_WATCHES:
                raise HTTPException(503, {"code": "device_busy"})
            await self._audit(owner, device, folder, "watch", "intended")
            watch = FolderWatch(folder, str(uuid4()))
            connection.watches[folder] = watch
            try:
                await connection.send({"type": "watch", "subscription": watch.subscription,
                                       "folder_id": str(folder)})
            except Exception as exc:
                connection.watches.pop(folder, None)
                await self._audit(owner, device, folder, "watch", "failed", sent=True)
                raise HTTPException(503, {"code": "device_disconnected"}) from exc
            await self._audit(owner, device, folder, "watch", "succeeded", sent=True)
        subscriber = Subscriber()
        watch.subscribers.add(subscriber)
        subscriber.offer({"paths": [], "reset": True})  # Every new reader rescans first.
        try:
            while True:
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), KEEPALIVE_SECONDS)
                except TimeoutError:
                    try:
                        current = await self._admit(owner, folder, "watch")
                    except HTTPException as exc:
                        yield {"unavailable": {"retryable": exc.status_code == 503}}
                        return
                    if current["epoch"] != str(connection.epoch) or connection.closed:
                        yield {"unavailable": {"retryable": True}}
                        return
                    yield None
                    continue
                if event is _CLOSED:
                    yield {"unavailable": {"retryable": True}}
                    return
                yield event
        finally:
            watch.subscribers.discard(subscriber)
            if not watch.subscribers and connection.watches.get(folder) is watch:
                del connection.watches[folder]
                if not connection.closed:
                    # The socket may be closing; the device then drops its watchers with it.
                    with suppress(Exception):
                        await connection.send({"type": "unwatch", "subscription": watch.subscription})
