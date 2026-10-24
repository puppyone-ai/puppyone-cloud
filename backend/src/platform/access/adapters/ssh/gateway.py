"""Standard SSH transport adapter. Workspace owns execution and cleanup."""

import asyncio
import base64
import logging
import signal
from contextlib import suppress
from pathlib import Path

import asyncssh

from src.infra.execution.runner import run_blocking

from .config import ssh_settings
from .hosted import build_hosted_ssh, public_key

logger = logging.getLogger(__name__)


class SshServer(asyncssh.SSHServer):
    def __init__(self, service, connections):
        self.service, self.connections = service, connections

    def connection_made(self, connection):
        self.connection = connection
        self.connections.add(connection)

    def connection_lost(self, exc):
        self.connections.discard(self.connection)

    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    async def validate_public_key(self, username, key):
        return await self.service.authenticate(username, key)


async def serve_shell(process, service):
    if process.subsystem:
        process.exit(1)
        return
    identity = process.get_extra_info("username")
    try:
        async with service.open(identity) as (connection, shell):
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

            async def input_frames():
                while True:
                    try:
                        data = await process.stdin.read(32768)
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
                    await asyncio.sleep(8)
                    await service.renew(connection, shell)

            async def output_frames():
                while True:
                    frame = await shell.receive()
                    if frame["type"] == "shell_exit":
                        return int(frame["status"])
                    if frame["type"] != "shell_data":
                        raise ConnectionError("Workspace shell disconnected")
                    output = process.stderr if frame["stream"] == "stderr" else process.stdout
                    output.write(base64.b64decode(frame["data"], validate=True))
                    await output.drain()

            async def disconnected():
                await process.wait_closed()
                raise ConnectionError("SSH client disconnected")

            async with asyncio.TaskGroup() as tasks:
                input_task = tasks.create_task(input_frames())
                renewal = tasks.create_task(heartbeat())
                monitor = tasks.create_task(disconnected())
                status = await output_frames()
                input_task.cancel()
                renewal.cancel()
                monitor.cancel()
        process.exit(status)
    except asyncio.CancelledError:
        process.close()
        raise
    except Exception:
        logger.exception("workspace_ssh_session_failed", extra={"connection": identity})
        process.exit(255)


async def start_gateway(service, host_key, *, host, port, connections=None, sessions=None):
    connections = connections if connections is not None else set()
    sessions = sessions if sessions is not None else set()

    async def session(process):
        task = asyncio.current_task()
        sessions.add(task)
        try:
            await serve_shell(process, service)
        finally:
            sessions.discard(task)

    return await asyncssh.create_server(
        lambda: SshServer(service, connections),
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
        agent_forwarding=False,
        x11_forwarding=False,
        allow_scp=False,
        max_pktsize=32768,
        window=262144,
    )


async def run():
    config = ssh_settings()
    service = build_hosted_ssh()
    service.endpoint()  # no implicit public hostname or permissive host key
    path = Path(config.host_key_file)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise RuntimeError("WORKSPACE_SSH_HOST_KEY_FILE must be a private persistent key file")
    host_key = asyncssh.read_private_key(path)
    if public_key(host_key.export_public_key()) != public_key(config.public_host_key):
        raise RuntimeError("SSH host key does not match the advertised public key")
    # Validate the additive schema before accepting public connections.
    async with asyncio.timeout(15):
        await run_blocking(service.connections.repository.pending)
        await run_blocking(service.grants.get, "00000000-0000-0000-0000-000000000000")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)

    async def reap():
        while not stop.is_set():
            try:
                await service.connections.reap()
            except Exception:
                logger.exception("workspace_ssh_reaper_failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), 10)

    connections, sessions = set(), set()
    gateway = await start_gateway(
        service,
        host_key,
        host=config.listen_host,
        port=config.listen_port,
        connections=connections,
        sessions=sessions,
    )
    reaper = asyncio.create_task(reap())
    try:
        await stop.wait()
    finally:
        gateway.close()
        await gateway.wait_closed()
        active = list(connections)
        for connection in active:
            connection.close()
        await asyncio.gather(
            *(connection.wait_closed() for connection in active), return_exceptions=True
        )
        # SSH transport closure does not imply the remote process stop has been
        # acknowledged. Keep Workspace transports alive until session cleanup ends.
        await asyncio.gather(*list(sessions), return_exceptions=True)
        reaper.cancel()
        await asyncio.gather(reaper, return_exceptions=True)
        await service.workspace.close()


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
