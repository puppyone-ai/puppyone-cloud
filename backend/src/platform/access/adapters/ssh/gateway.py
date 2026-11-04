"""Standard SSH transport adapter. Workspace owns execution and cleanup."""

import asyncio
import base64
import logging
import signal
import time
from contextlib import suppress
from pathlib import Path

import asyncssh

from src.infra.execution.runner import run_blocking
from src.platform.project.write_lease import install_storage_write_admission
from src.platform.workspace.errors import WorkspaceError

from .config import ssh_settings
from .hosted import build_hosted_ssh, public_key

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
# Dead clients behind a proxy leave no FIN; keepalives find them.
KEEPALIVE_SECONDS = 15
KEEPALIVE_COUNT = 4

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
# How long a refused command's client gets to read why before its connection closes.
CLOSE_NOTICE_SECONDS = 5
# Codes that end the session every command on a connection shares, not one command.
CONNECTION_ENDINGS = frozenset(
    {"access_denied", "workspace_connection_ended", "workbench_generation_changed"}
)


class ClientDisconnected(ConnectionError):
    """The SSH client went away: a normal end of a session, not a fault."""


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
    """What one SSH connection was admitted to, shared by every command over it.

    A client multiplexing commands over one connection (OpenSSH ControlMaster)
    signs in once: the first command admits the connection, the rest reuse it.
    Its heartbeat holds the session; when the server ends it, new commands are
    refused, running ones end and say why, and then the connection closes.
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


class SshServer(asyncssh.SSHServer):
    def __init__(self, service, connections, max_connections=None, admissions=None, closing=None):
        self.service, self.connections = service, connections
        self.max_connections = max_connections
        self.admissions = admissions if admissions is not None else {}
        self.closing = closing if closing is not None else set()

    def connection_made(self, connection):
        self.connection = connection
        if self.max_connections is not None and len(self.connections) >= self.max_connections:
            # Bound this process before authentication work; clients retry.
            logger.warning("workspace_ssh_gateway_at_capacity")
            connection.close()
            return
        self.connections.add(connection)
        self.admissions[connection] = ConnectionAdmission(self.service, connection)

    def connection_lost(self, exc):
        self.connections.discard(self.connection)
        admission = self.admissions.pop(self.connection, None)
        if admission is not None:
            task = asyncio.ensure_future(admission.close())
            self.closing.add(task)
            task.add_done_callback(self.closing.discard)

    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    async def validate_public_key(self, username, key):
        return await self.service.authenticate(username, key)


async def serve_shell(process, service, admission):
    if process.subsystem:
        process.exit(1)
        return
    identity = process.get_extra_info("username")

    async def notify(message):
        try:
            process.stderr.write(message.encode())
            await process.stderr.drain()
        except (BrokenPipeError, ConnectionError, asyncssh.Error) as exc:
            # The client left while it waited; stop waiting for it.
            raise ClientDisconnected("SSH client disconnected") from exc

    admission.begin()
    try:
        session = await admission.session_for(identity, notify)
        async with service.open(session, notify=notify) as (connection, shell):
            command = process.command or ""
            if len(command) > 32768:
                raise ValueError("Command too large")
            width, height, *_ = process.term_size
            await shell.control(
                "start",
                {
                    "command": command,
                    "term": process.term_type or "",
                    "size": [width or 80, height or 24],
                },
            )

            last_seen = time.monotonic()

            async def idle_limit():
                while True:
                    await asyncio.sleep(IDLE_CHECK_SECONDS)
                    if time.monotonic() - last_seen >= IDLE_SESSION_SECONDS:
                        raise ClientDisconnected("SSH session idle")

            async def input_frames():
                nonlocal last_seen
                while True:
                    try:
                        data = await process.stdin.read(32768)
                        last_seen = time.monotonic()
                    except asyncssh.TerminalSizeChanged as event:
                        await shell.control("resize", {"size": [event.width, event.height]})
                        continue
                    except asyncssh.SignalReceived as event:
                        if event.signal in {"INT", "TERM", "HUP", "KILL"}:
                            await shell.control("signal", {"signal": event.signal})
                        continue
                    if not data:
                        await shell.control("eof")
                        return
                    await shell.control("input", {"data": base64.b64encode(data).decode()})

            async def heartbeat():
                while True:
                    await asyncio.sleep(HEARTBEAT_SECONDS)
                    await service.renew(connection, shell)

            async def output_frames():
                nonlocal last_seen
                while True:
                    frame = await shell.receive()
                    last_seen = time.monotonic()
                    if frame["type"] == "shell_exit":
                        return int(frame["status"])
                    if frame["type"] != "shell_data":
                        raise ConnectionError("Workspace shell disconnected")
                    output = process.stderr if frame["stream"] == "stderr" else process.stdout
                    output.write(base64.b64decode(frame["data"], validate=True))
                    await output.drain()

            async def disconnected():
                await process.wait_closed()
                raise ClientDisconnected("SSH client disconnected")

            async with asyncio.TaskGroup() as tasks:
                input_task = tasks.create_task(input_frames())
                renewal = tasks.create_task(heartbeat())
                monitor = tasks.create_task(disconnected())
                idle = tasks.create_task(idle_limit())
                ended = tasks.create_task(admission.until_ended())
                status = await output_frames()
                input_task.cancel()
                renewal.cancel()
                monitor.cancel()
                idle.cancel()
                ended.cancel()
        process.exit(status)
    except asyncio.CancelledError:
        process.close()
        raise
    except Exception as exc:
        code, hung_up = session_ending(exc)
        if hung_up:
            logger.info("workspace_ssh_client_disconnected", extra={"connection": identity})
            process.close()
            return
        if code:
            # Product codes (paused, replaced, limit, ended, denied) are safe
            # and actionable; they are expected outcomes, not faults.
            logger.warning(
                "workspace_ssh_session_refused", extra={"connection": identity, "code": code}
            )
            message = SESSION_MESSAGES.get(code, code)
            if code in CONNECTION_ENDINGS:
                # Close once this command has said why, rather than refuse every
                # later one until the next heartbeat notices.
                admission.stop(code)
        else:
            logger.exception("workspace_ssh_session_failed", extra={"connection": identity})
            message = UNEXPECTED_ENDING
        with suppress(Exception):
            await notify(f"PuppyOne: {message}\r\n")
        process.exit(1 if code else 255)
        if admission.ended:
            # The connection closes after this command; let the client take in why first.
            with suppress(Exception):
                async with asyncio.timeout(CLOSE_NOTICE_SECONDS):
                    await process.wait_closed()
    finally:
        admission.end()


async def start_gateway(
    service,
    host_key,
    *,
    host,
    port,
    connections=None,
    sessions=None,
    max_connections=None,
    closing=None,
):
    connections = connections if connections is not None else set()
    sessions = sessions if sessions is not None else set()
    closing = closing if closing is not None else set()
    admissions = {}

    async def session(process):
        task = asyncio.current_task()
        sessions.add(task)
        try:
            await serve_shell(process, service, admissions[process.channel.get_connection()])
        finally:
            sessions.discard(task)

    return await asyncssh.create_server(
        lambda: SshServer(service, connections, max_connections, admissions, closing),
        host,
        port,
        server_host_keys=[host_key],
        encoding=None,
        process_factory=session,
        password_auth=False,
        kbdint_auth=False,
        public_key_auth=True,
        line_editor=False,  # The remote POSIX PTY owns editing, echo and control keys.
        login_timeout=15,
        keepalive_interval=KEEPALIVE_SECONDS,
        keepalive_count_max=KEEPALIVE_COUNT,
        agent_forwarding=False,
        x11_forwarding=False,
        allow_scp=False,
        max_pktsize=32768,
        window=262144,
    )


def load_host_key(config):
    """Host identity from a secret variable, or a private persistent key file."""
    if config.host_key.get_secret_value():
        return asyncssh.import_private_key(config.host_key.get_secret_value())
    path = Path(config.host_key_file)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise RuntimeError(
            "Configure WORKSPACE_SSH_HOST_KEY or a private persistent WORKSPACE_SSH_HOST_KEY_FILE"
        )
    return asyncssh.read_private_key(path)


async def stop_gateway(gateway, connections, sessions=(), closing=()):
    """Stop accepting, end every connection, and wait until each has cleaned up.

    A server counts as closed only once its connections are, and a client keeps one
    open between commands: they are closed first, or shutdown waits for every client.
    """
    gateway.close()
    active = list(connections)
    for connection in active:
        connection.close()
    await asyncio.gather(
        *(connection.wait_closed() for connection in active), return_exceptions=True
    )
    await gateway.wait_closed()
    # SSH transport closure does not imply the remote process stop has been
    # acknowledged. Keep Workspace transports alive until session cleanup ends.
    await asyncio.gather(*list(sessions), return_exceptions=True)
    # Each connection's session is finished before the process lets go.
    await asyncio.gather(*list(closing), return_exceptions=True)


async def run():
    config = ssh_settings()
    service = build_hosted_ssh()
    service.endpoint()  # no implicit public hostname or permissive host key
    host_key = load_host_key(config)
    if public_key(host_key.export_public_key()) != public_key(config.public_host_key):
        raise RuntimeError("SSH host key does not match the advertised public key")
    # Validate the additive schema before accepting public connections.
    async with asyncio.timeout(15):
        await run_blocking(service.connections.repository.pending)
        await run_blocking(service.grants.get, "00000000-0000-0000-0000-000000000000")
        _, access_sessions = service.lifecycle()
        await access_sessions.ready()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)

    # Lapsed or revoked connections are reclaimed by the worker's sandbox
    # module, scoped to its pool; this process only ends its own sessions.
    connections, sessions, closing = set(), set(), set()
    gateway = await start_gateway(
        service,
        host_key,
        host=config.listen_host,
        port=config.listen_port,
        connections=connections,
        sessions=sessions,
        max_connections=config.max_connections,
        closing=closing,
    )
    try:
        await stop.wait()
    finally:
        await stop_gateway(gateway, connections, sessions, closing)
        await service.workspace.close()


def main():
    logging.basicConfig(level=logging.INFO)
    install_storage_write_admission()
    asyncio.run(run())


if __name__ == "__main__":
    main()
