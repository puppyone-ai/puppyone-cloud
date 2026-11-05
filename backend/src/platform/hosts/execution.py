"""Terminals and Agents that run on the owner's own computer, relayed by the gateway.

The accessing client attaches with a WebSocket to the process that holds the
device socket. Each session is a ``host_exec_sessions`` row fenced by the device
connection epoch and renewed every few seconds; a reconnect, a revoked device or
a withdrawn capability ends it at the next renewal. The host decides what runs:
the client names a launcher from a fixed list, never a command line.
"""

import asyncio
import base64
import binascii
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from fastapi import WebSocket, WebSocketDisconnect

from src.infra.execution.runner import run_blocking

logger = logging.getLogger(__name__)

EXEC_LEASE_SECONDS = 30
EXEC_RENEW_SECONDS = 10
EXEC_START_TIMEOUT = 20
EXEC_QUEUE = 512
MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
# The host maps these to its own installed tools; nothing else can be started.
LAUNCHERS = frozenset({"shell", "codex", "claude", "cursor", "opencode", "pi", "hermes"})
SIGNALS = frozenset({"INT", "TERM", "HUP", "KILL"})


class ExecRefused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class ExecSession:
    id: str
    device: UUID
    folder: UUID
    kind: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=EXEC_QUEUE))
    started: asyncio.Future | None = None
    exited: bool = False
    overflowed: bool = False

    def deliver(self, frame: dict):
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # A client that cannot keep up loses the session, never the device socket.
            self.overflowed = True
            with suppress(asyncio.QueueEmpty):
                while True:
                    self.queue.get_nowait()
            self.queue.put_nowait({"type": "error", "code": "slow_client"})


def parse_start(frame) -> dict:
    if not isinstance(frame, dict) or frame.get("type") != "start":
        raise ExecRefused("invalid_start")
    kind, launcher = frame.get("kind"), frame.get("launcher", "shell")
    if kind not in {"shell", "agent"} or launcher not in LAUNCHERS or (kind == "agent") == (launcher == "shell"):
        raise ExecRefused("invalid_start")
    cols, rows = frame.get("cols", 80), frame.get("rows", 24)
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (cols, rows)):
        raise ExecRefused("invalid_start")
    return {"kind": kind, "launcher": launcher, "cols": max(20, min(cols, 400)), "rows": max(8, min(rows, 120))}


def device_frame_for(session: ExecSession, frame) -> dict | None:
    """Translate one client frame to a device frame; None ends the session."""
    if not isinstance(frame, dict):
        raise ExecRefused("invalid_frame")
    kind = frame.get("type")
    if kind == "input":
        data = frame.get("data")
        try:
            raw = base64.b64decode(data, validate=True) if isinstance(data, str) else None
        except (binascii.Error, ValueError):
            raw = None
        if raw is None or len(raw) > MAX_INPUT_BYTES:
            raise ExecRefused("invalid_frame")
        return {"type": "exec_input", "session": session.id, "data": data}
    if kind == "resize":
        cols, rows = frame.get("cols"), frame.get("rows")
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (cols, rows)):
            raise ExecRefused("invalid_frame")
        return {"type": "exec_resize", "session": session.id, "cols": max(20, min(cols, 400)),
                "rows": max(8, min(rows, 120))}
    if kind == "signal" and frame.get("signal") in SIGNALS:
        return {"type": "exec_signal", "session": session.id, "signal": frame["signal"]}
    if kind == "close":
        return None
    raise ExecRefused("invalid_frame")


def client_frame_from_device(message: dict) -> dict | None:
    """The client-facing form of a device exec frame, or None if malformed."""
    kind = message.get("type")
    if kind == "exec_output" and isinstance(message.get("data"), str) and len(message["data"]) <= MAX_OUTPUT_BYTES * 2:
        return {"type": "output", "data": message["data"]}
    if kind == "exec_exit":
        status = message.get("status")
        return {"type": "exit", "status": status if isinstance(status, int) and not isinstance(status, bool) else None}
    return None


class ExecRelay:
    """Owns the client side of exec sessions for one HostGateway."""

    def __init__(self, gateway):
        self.gateway = gateway

    async def _command(self, name, **params):
        return await run_blocking(self.gateway.repository.command, name, **params)

    async def run(self, owner: str, folder: UUID, websocket: WebSocket, start: dict):
        from .gateway import GATEWAY_INSTANCE

        session_id = str(uuid4())
        try:
            opened = await self._command("host_exec_open", p_actor=owner, p_folder=str(folder),
                                         p_session=session_id, p_kind=start["kind"],
                                         p_gateway=GATEWAY_INSTANCE, p_lease_seconds=EXEC_LEASE_SECONDS)
        except Exception as exc:
            code = getattr(exc, "code", "")
            raise ExecRefused({"55000": "device_offline", "22023": "session_limit"}.get(code, "not_allowed")) from exc
        device = UUID(opened["device_id"])
        connection = self.gateway.connections.get(device)
        if connection is None or connection.closed or str(connection.epoch) != opened["epoch"]:
            await self._close_row(session_id, None)
            raise ExecRefused("device_offline")
        session = ExecSession(session_id, device, folder, start["kind"])
        session.started = asyncio.get_running_loop().create_future()
        connection.exec_sessions[session_id] = session
        status = None
        try:
            await self.gateway._audit(owner, device, folder, start["kind"], "intended")
            await connection.send({"type": "exec_open", "session": session_id, "folder_id": str(folder),
                                   "kind": start["kind"], "launcher": start["launcher"],
                                   "cols": start["cols"], "rows": start["rows"]})
            try:
                failure = await asyncio.wait_for(session.started, EXEC_START_TIMEOUT)
            except TimeoutError:
                failure = "device_timeout"
            if failure:
                await self.gateway._audit(owner, device, folder, start["kind"], "failed", sent=True)
                raise ExecRefused(failure)
            await self.gateway._audit(owner, device, folder, start["kind"], "succeeded", sent=True)
            await websocket.send_json({"type": "started", "session": session_id})
            logger.info("host_exec_started", extra={"kind": start["kind"], "launcher": start["launcher"]})
            status = await self._pump(connection, session, websocket)
        finally:
            connection.exec_sessions.pop(session_id, None)
            if not session.exited and not connection.closed:
                with suppress(Exception):
                    await connection.send({"type": "exec_close", "session": session_id})
            await self._close_row(session_id, status)

    async def _close_row(self, session_id: str, status):
        with suppress(Exception):
            await self._command("host_exec_close", p_session=session_id, p_exit_status=status)

    async def _pump(self, connection, session: ExecSession, websocket: WebSocket):
        from .gateway import GATEWAY_INSTANCE

        exit_status = {"value": None}

        async def from_client():
            while True:
                frame = device_frame_for(session, await websocket.receive_json())
                if frame is None:
                    return
                await connection.send(frame)

        async def to_client():
            while True:
                frame = await session.queue.get()
                await websocket.send_json(frame)
                if frame["type"] == "exit":
                    exit_status["value"] = frame["status"]
                    return
                if frame["type"] == "error":
                    return

        async def renew():
            while True:
                await asyncio.sleep(EXEC_RENEW_SECONDS)
                if not await self._command("host_exec_renew", p_session=session.id, p_gateway=GATEWAY_INSTANCE,
                                           p_lease_seconds=EXEC_LEASE_SECONDS):
                    session.deliver({"type": "error", "code": "not_allowed"})
                    return

        tasks = [asyncio.create_task(job()) for job in (from_client, to_client, renew)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                error = task.exception()
                if isinstance(error, ExecRefused):
                    with suppress(Exception):
                        await websocket.send_json({"type": "error", "code": error.code})
                elif error and not isinstance(error, WebSocketDisconnect):
                    logger.warning("host_exec_pump_failed", extra={"error": type(error).__name__})
            # A renewal that failed queued an error; give the client a moment to receive it.
            if not tasks[1].done():
                with suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(tasks[1]), 1)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return exit_status["value"]


def device_exec_frame(connection, message: dict) -> bool:
    """Route one exec frame from the device to its session. False if not an exec frame."""
    kind = message.get("type")
    if kind not in {"exec_opened", "exec_failed", "exec_output", "exec_exit"}:
        return False
    session = connection.exec_sessions.get(message.get("session")) if isinstance(message.get("session"), str) else None
    if session is None:
        return True  # A late frame for a session that already ended.
    if kind == "exec_opened":
        if session.started and not session.started.done():
            session.started.set_result(None)
    elif kind == "exec_failed":
        session.exited = True  # Nothing runs on the host; there is nothing to close.
        code = message.get("error") if message.get("error") in {
            "folder_unavailable", "not_allowed", "launcher_unavailable", "session_limit"} else "start_failed"
        if session.started and not session.started.done():
            session.started.set_result(code)
        else:
            session.deliver({"type": "error", "code": code})
    else:
        frame = client_frame_from_device(message)
        if frame is None:
            raise ValueError("Invalid exec frame")
        if frame["type"] == "exit":
            session.exited = True
        session.deliver(frame)
    return True


def end_sessions(connection, code: str):
    """The device went away: every client learns why, then its session ends."""
    for session in list(connection.exec_sessions.values()):
        if session.started and not session.started.done():
            session.started.set_result(code)
        session.deliver({"type": "error", "code": code})


def token_from(websocket: WebSocket) -> tuple[str, str | None]:
    """Bearer token from the header or a ``version.bearer.<jwt>`` subprotocol."""
    header = websocket.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), None
    for raw in websocket.headers.get("sec-websocket-protocol", "").split(","):
        protocol = raw.strip()
        if protocol.startswith("version.bearer.") and len(protocol) > len("version.bearer."):
            return protocol[len("version.bearer."):], "version.v1"
    return "", None


async def serve_exec(gateway, websocket: WebSocket, folder: UUID, authenticate):
    """One client terminal: authenticate, start on the host, relay until either side ends."""
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
        start = parse_start(await asyncio.wait_for(websocket.receive_json(), 10))
        await gateway.exec.run(str(user.user_id), folder, websocket, start)
    except ExecRefused as refused:
        with suppress(Exception):
            await websocket.send_json({"type": "error", "code": refused.code})
    except (WebSocketDisconnect, TimeoutError, ValueError):
        pass
    finally:
        with suppress(Exception):
            await websocket.close()
