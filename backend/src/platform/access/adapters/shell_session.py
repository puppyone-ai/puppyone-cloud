"""One admitted connection running commands in a Hosted Workspace, whatever carries it.

The SSH gateway and the HTTPS command endpoint both sign a client in once, hold
its Access session while it works, and run each command in its own shell. Only
how bytes reach the client differs, so that is all each transport supplies.
"""

import asyncio
import base64
import logging
import time
from contextlib import suppress

from src.platform.workspace.errors import WorkspaceError

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 8
# An open session keeps its machine running, so a forgotten one must not last.
IDLE_SESSION_SECONDS = 1800
IDLE_CHECK_SECONDS = 30
# The connection's own heartbeat, inside its Access session's 45-second lease.
CONNECTION_HEARTBEAT_SECONDS = 15
# A connection with no command running holds no machine, but still ends in time,
# as a client keeping it open (OpenSSH ControlPersist) expects.
IDLE_CONNECTION_SECONDS = 1800
# Infrastructure failures a renewal survives; the lease decides access meanwhile.
CONNECTION_RENEWAL_ATTEMPTS = 3
MAX_COMMAND_BYTES = 32768

SESSION_MESSAGES = {
    "workbench_not_running": "the workspace is not running and this credential cannot resume it.",
    "workbench_generation_changed": "the workspace was replaced; request a new SSH credential.",
    "workspace_connection_limit": "too many open SSH sessions for this workspace; close one and retry.",
    "workspace_connection_ended": (
        "this session was ended: the workspace owner disconnected it or the workspace stopped."
    ),
    "workspace_preparing": "the workspace is still being prepared; run the command again in a minute.",
    "access_denied": (
        "access to this workspace is not available: it was revoked, expired or ended by "
        "the owner, or is already in use by another session."
    ),
}
UNEXPECTED_ENDING = "the session ended unexpectedly; reconnect to continue."
# Codes that end the session every command on a connection shares, not one command.
CONNECTION_ENDINGS = frozenset(
    {"access_denied", "workspace_connection_ended", "workbench_generation_changed"}
)


class ClientDisconnected(ConnectionError):
    """The client went away: a normal end of a session, not a fault."""


def _leaves(error):
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            yield from _leaves(inner)
    else:
        yield error


def session_ending(error):
    """Classify a session error as ``(product code, client hung up)``.

    Concurrent session tasks surface failures as an exception group; an
    expected refusal anywhere in it decides the message the user sees.
    """
    leaves = list(_leaves(error))
    for leaf in leaves:
        if isinstance(leaf, WorkspaceError):
            detail = leaf.detail
            return (detail.get("code") if isinstance(detail, dict) else None) or (
                "workspace_unavailable"
            ), False
    return None, bool(leaves) and all(isinstance(leaf, ClientDisconnected) for leaf in leaves)


class ConnectionAdmission:
    """What one connection was admitted to, shared by every command over it.

    A client multiplexing commands over one connection (OpenSSH ControlMaster)
    signs in once: the first command admits the connection, the rest reuse it.
    Its heartbeat holds the session; when the server ends it, new commands are
    refused, running ones end and say why, and then the connection closes.
    ``connection`` is anything with ``close()``: an SSH connection, or the
    request an HTTPS command arrived on.
    """

    def __init__(self, service, connection):
        self.service, self.connection = service, connection
        self.lock = asyncio.Lock()
        self.session = None
        self.ended = None
        self.ending = asyncio.Event()
        self.commands = 0
        self.last_seen = time.monotonic()
        self.keeper = None

    async def session_for(self, identity, notify):
        async with self.lock:
            if self.ended:
                raise WorkspaceError(403, {"code": self.ended})
            if self.session is None:
                self.session = await self.service.admit(identity, notify=notify)
                self.keeper = asyncio.create_task(self.keep())
            return self.session

    def begin(self):
        self.commands += 1
        self.last_seen = time.monotonic()

    def end(self):
        self.commands -= 1
        self.last_seen = time.monotonic()
        if self.ended and self.commands == 0:
            self.connection.close()

    def stop(self, code):
        if not self.ended:
            self.ended = code
            self.ending.set()
        if self.commands == 0:
            self.connection.close()

    async def until_ended(self):
        """A running command's own lease may outlive the session, as an owner's does."""
        await self.ending.wait()
        raise WorkspaceError(403, {"code": self.ended})

    async def keep(self):
        failures = 0
        while True:
            await asyncio.sleep(CONNECTION_HEARTBEAT_SECONDS)
            if self.commands == 0 and time.monotonic() - self.last_seen >= IDLE_CONNECTION_SECONDS:
                logger.info("workspace_ssh_connection_idle")
                self.stop("workspace_connection_ended")
                return
            try:
                await self.service.renew_session(self.session)
                failures = 0
            except WorkspaceError as exc:
                # Revoked, expired, replaced or no longer allowed: an expected ending.
                code, _ = session_ending(exc)
                logger.warning("workspace_ssh_connection_ended", extra={"code": code})
                self.stop(code)
                return
            except Exception:
                failures += 1
                if failures >= CONNECTION_RENEWAL_ATTEMPTS:
                    logger.exception("workspace_ssh_connection_renewal_failed")
                    self.stop("workspace_unavailable")
                    return

    async def close(self):
        if self.keeper:
            self.keeper.cancel()
            with suppress(asyncio.CancelledError):
                await self.keeper
        if self.session is not None:
            try:
                await self.service.finish(self.session)
            except Exception:
                # The lease lapses and cleanup closes the session instead.
                logger.warning("workspace_ssh_connection_finish_pending")


async def run_command(
    service,
    admission,
    session,
    command,
    *,
    write,
    notify,
    term="",
    size=(80, 24),
    relay_input=None,
    disconnected=None,
):
    """Run ``command`` in its own shell under ``session`` and return its exit status.

    ``write(stream, data)`` delivers output as it comes. ``relay_input(shell, seen)``
    forwards the client's input and calls ``seen()`` on each; without it the
    command reads end of file at once. ``disconnected()`` raises
    ``ClientDisconnected`` when the client goes away, for a transport that
    learns it that way rather than by being cancelled.
    """
    async with service.open(session, notify=notify) as (connection, shell):
        if len(command) > MAX_COMMAND_BYTES:
            raise ValueError("Command too large")
        await shell.control("start", {"command": command, "term": term, "size": [size[0], size[1]]})

        last_seen = time.monotonic()

        def seen():
            nonlocal last_seen
            last_seen = time.monotonic()

        async def idle_limit():
            while True:
                await asyncio.sleep(IDLE_CHECK_SECONDS)
                if time.monotonic() - last_seen >= IDLE_SESSION_SECONDS:
                    raise ClientDisconnected("Session idle")

        async def no_input():
            await shell.control("eof")

        async def heartbeat():
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                await service.renew(connection, shell)

        async def output_frames():
            while True:
                frame = await shell.receive()
                seen()
                if frame["type"] == "shell_exit":
                    return int(frame["status"])
                if frame["type"] != "shell_data":
                    raise ConnectionError("Workspace shell disconnected")
                await write(frame["stream"], base64.b64decode(frame["data"], validate=True))

        async with asyncio.TaskGroup() as tasks:
            watched = [
                tasks.create_task(relay_input(shell, seen) if relay_input else no_input()),
                tasks.create_task(heartbeat()),
                tasks.create_task(idle_limit()),
                tasks.create_task(admission.until_ended()),
            ]
            if disconnected:
                watched.append(tasks.create_task(disconnected()))
            status = await output_frames()
            for task in watched:
                task.cancel()
    return status
