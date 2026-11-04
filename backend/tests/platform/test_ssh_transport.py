"""Real SSH transport against the native PTY helper; provider lifecycle tested separately."""

import asyncio
import base64
import json
import os
import shutil
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncssh
import pytest

from src.platform.access.adapters.ssh import gateway as ssh_gateway
from src.platform.access.adapters.ssh.gateway import (
    ClientDisconnected,
    session_ending,
    start_gateway,
    stop_gateway,
)
from src.platform.access.agent_links import CONNECTION_OPTIONS, PROXY_HELPER
from src.platform.workspace.errors import WorkspaceError

HELPER = Path(__file__).parents[2] / "agent-worker/workbench-shell.py"


class NativeShell:
    """Test-only local transport. Never selected by the product provider registry."""

    def __init__(self, directory):
        self.directory = directory
        self.process = None

    async def control(self, action, value=None):
        if action == "start":
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-u",
                str(HELPER),
                base64.b64encode(json.dumps(value).encode()).decode(),
                cwd=self.directory,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            self.process.stdin.write(
                json.dumps({"action": action, **(value or {})}).encode() + b"\n"
            )
            await self.process.stdin.drain()
        return {}

    async def receive(self):
        value = await self.process.stdout.readline()
        return json.loads(value) if value else {"type": "disconnected"}

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()


class NativeAccess:
    def __init__(self, key, directory):
        self.key, self.directory, self.used, self.closed = key, directory, False, False
        self.admitted, self.opened, self.renewed, self.finished = 0, 0, 0, []

    async def authenticate(self, identity, key):
        return (
            identity == "fixture"
            and key.export_public_key() == self.key.export_public_key()
            and not self.used
        )

    async def admit(self, identity, notify=None):
        # A credential admits one connection; its commands reuse the admission.
        if self.used:
            raise PermissionError("Used")
        self.used = True
        self.admitted += 1
        return {"id": identity}

    open_error = None

    @asynccontextmanager
    async def open(self, session, notify=None):
        self.opened += 1
        if self.open_error:
            raise self.open_error
        if getattr(self, "resume_notice", None):
            await notify(self.resume_notice)
        shell = NativeShell(self.directory)
        try:
            yield {"id": session["id"]}, shell
        finally:
            await shell.close()
            self.closed = True

    renewal_error = None
    session_error = None

    async def renew(self, *args):
        if self.renewal_error:
            raise self.renewal_error

    async def renew_session(self, session):
        self.renewed += 1
        if self.session_error:
            raise self.session_error

    async def finish(self, session):
        self.finished.append(session["id"])


@pytest.fixture
async def transport(tmp_path):
    host = asyncssh.generate_private_key("ssh-ed25519")
    key = asyncssh.generate_private_key("ssh-ed25519")
    service = NativeAccess(key, tmp_path)
    gateway = await start_gateway(service, host, host="127.0.0.1", port=0)
    port = gateway.get_port()
    known = tmp_path / "known_hosts"
    known.write_text(f"[127.0.0.1]:{port} " + host.export_public_key().decode())
    try:
        async with await asyncssh.connect(
            "127.0.0.1",
            port=port,
            username="fixture",
            client_keys=[key],
            known_hosts=str(known),
            encoding=None,
        ) as connection:
            yield connection, service
    finally:
        gateway.close()
        await gateway.wait_closed()


async def test_native_ssh_streams_binary_stderr_and_exit_status(transport):
    connection, service = transport
    data = b"\x00\xffbinary\n" * 8192
    async with asyncio.timeout(15):
        result = await connection.run("cat; printf error >&2; exit 9", input=data, encoding=None)
    assert result.stdout == data
    assert result.stderr == b"error"
    assert result.exit_status == 9
    assert service.closed


async def test_native_ssh_terminal_context_and_resize(transport):
    connection, service = transport
    proc = await connection.create_process(term_type="xterm", term_size=(80, 24), encoding=None)
    proc.stdin.write(b"mkdir nested; cd nested; export TOKEN=kept\n")
    proc.stdin.write(b"printf '%s' \"$TOKEN\" > ../result; printf '\\nFIRST-DONE\\n'\n")
    async with asyncio.timeout(15):
        await proc.stdout.readuntil(b"\r\nFIRST-DONE\r\n")
    assert (service.directory / "result").read_text() == "kept"
    proc.change_terminal_size(120, 42)
    proc.stdin.write(b"stty size; printf '\\nSECOND-DONE\\n'\n")
    async with asyncio.timeout(15):
        result = await proc.stdout.readuntil(b"\r\nSECOND-DONE\r\n")
    assert b"42 120" in result
    proc.stdin.write(b"exit\n")
    async with asyncio.timeout(15):
        await proc.wait_closed()
    assert proc.exit_status == 0


async def test_forwarding_and_sftp_are_not_granted(transport):
    connection, _ = transport
    with pytest.raises(asyncssh.ChannelOpenError):
        await connection.open_connection("localhost", 80)
    with pytest.raises((asyncssh.ChannelOpenError, asyncssh.SFTPError, asyncssh.ConnectionLost)):
        await connection.start_sftp_client()


async def test_remote_pty_receives_raw_keystroke_without_newline(transport):
    connection, _ = transport
    proc = await connection.create_process(term_type="xterm", encoding=None)
    proc.stdin.write(
        b"stty -icanon -echo min 1 time 0; printf '\\nRAW-READY\\n'; "
        b"dd bs=1 count=1 2>/dev/null | od -An -tu1; printf '\\nRAW-DONE\\n'; exit\n"
    )
    async with asyncio.timeout(10):
        await proc.stdout.readuntil(b"\r\nRAW-READY\r\n")
        proc.stdin.write(b"A")
        data = await proc.stdout.readuntil(b"\r\nRAW-DONE\r\n")
        assert b"65" in data
        await proc.wait_closed()
    assert proc.exit_status == 0


async def _ended_by(transport, monkeypatch, error):
    connection, service = transport
    monkeypatch.setattr(ssh_gateway, "HEARTBEAT_SECONDS", 0.05)
    service.renewal_error = error
    async with asyncio.timeout(15):
        result = await connection.run("sleep 30", encoding=None)
    assert service.closed  # Workspace cleanup ran before the user was told.
    return result


async def test_an_owner_disconnect_tells_the_user_why_the_session_ended(
    transport, monkeypatch, caplog
):
    result = await _ended_by(
        transport, monkeypatch, WorkspaceError(409, {"code": "workspace_connection_ended"})
    )
    assert b"PuppyOne: this session was ended" in result.stderr
    assert result.exit_status == 1
    assert "workspace_ssh_session_failed" not in caplog.text


async def test_revoked_access_mid_session_is_an_expected_ending(transport, monkeypatch):
    result = await _ended_by(transport, monkeypatch, WorkspaceError(403, {"code": "access_denied"}))
    assert b"PuppyOne: access to this workspace is not available" in result.stderr
    assert result.exit_status == 1


async def test_an_unexpected_fault_still_tells_the_user(transport, monkeypatch, caplog):
    result = await _ended_by(transport, monkeypatch, RuntimeError("database unavailable"))
    assert b"PuppyOne: the session ended unexpectedly" in result.stderr
    assert b"database" not in result.stderr  # Internal details never reach the client.
    assert result.exit_status == 255
    assert "workspace_ssh_session_failed" in caplog.text


async def test_a_client_hangup_is_not_logged_as_a_failure(transport, caplog):
    connection, service = transport
    process = await connection.create_process("sleep 30", encoding=None)
    await asyncio.sleep(0.5)
    process.close()
    connection.close()
    async with asyncio.timeout(15):
        while not service.closed:
            await asyncio.sleep(0.05)
    assert "workspace_ssh_session_failed" not in caplog.text


def test_session_endings_are_classified_through_task_groups():
    ended = WorkspaceError(409, {"code": "workspace_connection_ended"})
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [ClientDisconnected(), ended])])
    assert session_ending(nested) == ("workspace_connection_ended", False)
    assert session_ending(ExceptionGroup("g", [ClientDisconnected()])) == (None, True)
    assert session_ending(ExceptionGroup("g", [ClientDisconnected(), RuntimeError()])) == (
        None,
        False,
    )
    assert session_ending(RuntimeError()) == (None, False)


async def test_commands_over_one_connection_sign_in_once(transport):
    connection, service = transport
    async with asyncio.timeout(15):
        for number in range(3):
            result = await connection.run(f"printf {number}", encoding=None)
            assert result.stdout == str(number).encode()
        together = await asyncio.gather(
            *(connection.run(f"printf {number}", encoding=None) for number in range(3, 6))
        )
    assert [result.stdout for result in together] == [b"3", b"4", b"5"]
    # One admission for the connection; each command is its own shell.
    assert service.admitted == 1
    assert service.opened == 6


async def test_the_server_ends_a_connection_whose_access_ended(transport, monkeypatch):
    connection, service = transport
    monkeypatch.setattr(ssh_gateway, "CONNECTION_HEARTBEAT_SECONDS", 0.05)
    await connection.run("true", encoding=None)
    service.session_error = WorkspaceError(403, {"code": "access_denied"})
    async with asyncio.timeout(15):
        await connection.wait_closed()
        while not service.finished:
            await asyncio.sleep(0.05)
    assert service.finished == ["fixture"]


async def test_a_command_refused_because_access_ended_closes_the_connection(transport):
    connection, service = transport
    await connection.run("true", encoding=None)
    service.open_error = WorkspaceError(403, {"code": "access_denied"})
    async with asyncio.timeout(15):
        result = await connection.run("true", encoding=None)
        assert result.exit_status == 1
        assert b"PuppyOne: access to this workspace is not available" in result.stderr
        # Not held open until the heartbeat notices: the next command signs in again.
        await connection.wait_closed()
        while not service.finished:
            await asyncio.sleep(0.05)
    assert service.finished == ["fixture"]


async def test_a_running_command_ends_with_its_connections_session(transport, monkeypatch):
    connection, service = transport
    monkeypatch.setattr(ssh_gateway, "CONNECTION_HEARTBEAT_SECONDS", 0.05)
    process = await connection.create_process("echo started; sleep 30", encoding=None)
    async with asyncio.timeout(15):
        assert await process.stdout.readline() == b"started\n"
        # The command's own lease still renews, as one under an owner's credential does.
        service.session_error = WorkspaceError(403, {"code": "access_denied"})
        await process.wait_closed()
        assert process.exit_status == 1
        assert b"PuppyOne: access to this workspace is not available" in (
            await process.stderr.read()
        )
        await connection.wait_closed()
    assert service.closed


async def test_a_command_refused_for_now_keeps_the_connection(transport):
    connection, service = transport
    service.open_error = WorkspaceError(503, {"code": "workspace_preparing"})
    async with asyncio.timeout(15):
        refused = await connection.run("true", encoding=None)
        assert refused.exit_status == 1
        service.open_error = None
        result = await connection.run("printf ready", encoding=None)
    assert result.stdout == b"ready"
    assert service.admitted == 1


async def test_shutdown_does_not_wait_for_clients_to_leave(tmp_path):
    host = asyncssh.generate_private_key("ssh-ed25519")
    key = asyncssh.generate_private_key("ssh-ed25519")
    service = NativeAccess(key, tmp_path)
    connections, sessions, closing = set(), set(), set()
    gateway = await start_gateway(
        service,
        host,
        host="127.0.0.1",
        port=0,
        connections=connections,
        sessions=sessions,
        closing=closing,
    )
    client = await asyncssh.connect(
        "127.0.0.1",
        port=gateway.get_port(),
        username="fixture",
        client_keys=[key],
        known_hosts=None,
        encoding=None,
    )
    # A multiplexing client keeps its connection open between commands.
    await client.run("true")
    async with asyncio.timeout(5):
        await stop_gateway(gateway, connections, sessions, closing)
        await client.wait_closed()
    assert service.finished == ["fixture"]


async def test_a_connection_left_idle_is_closed_and_its_session_finished(transport, monkeypatch):
    connection, service = transport
    monkeypatch.setattr(ssh_gateway, "CONNECTION_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(ssh_gateway, "IDLE_CONNECTION_SECONDS", 0.2)
    await connection.run("true", encoding=None)
    async with asyncio.timeout(15):
        await connection.wait_closed()
        while not service.finished:
            await asyncio.sleep(0.05)
    assert service.renewed >= 1


async def test_an_infrastructure_blip_does_not_end_the_connection(transport, monkeypatch):
    connection, service = transport
    monkeypatch.setattr(ssh_gateway, "CONNECTION_HEARTBEAT_SECONDS", 0.05)
    await connection.run("true", encoding=None)
    service.session_error = RuntimeError("database unavailable")
    await asyncio.sleep(0.08)  # One failed renewal, fewer than the connection survives.
    service.session_error = None
    result = await connection.run("printf still-here", encoding=None)
    assert result.stdout == b"still-here"
    assert service.admitted == 1


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the OpenSSH client")
async def test_the_command_agents_are_given_signs_in_once_for_every_command(tmp_path):
    host = asyncssh.generate_private_key("ssh-ed25519")
    key = asyncssh.generate_private_key("ssh-ed25519")
    service = NativeAccess(key, tmp_path)

    class Counted(set):
        made = 0

        def add(self, item):
            Counted.made += 1
            super().add(item)

    gateway = await start_gateway(service, host, host="127.0.0.1", port=0, connections=Counted())
    port = gateway.get_port()
    identity = tmp_path / "id"
    key.write_private_key(str(identity))
    os.chmod(identity, 0o600)
    known = tmp_path / "known_hosts"
    known.write_text(f"[127.0.0.1]:{port} " + host.export_public_key().decode())
    # The agent's options exactly, with a socket path short enough for any test directory.
    options = CONNECTION_OPTIONS.replace(
        "~/.ssh/puppyone-%C", f"/tmp/pk-{uuid4().hex[:8]}-%C"
    ).split()
    ssh = [
        "ssh",
        "-F",
        "/dev/null",
        "-i",
        str(identity),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        f"UserKnownHostsFile={known}",
        "-o",
        "BatchMode=yes",
        *options,
        "-p",
        str(port),
    ]

    async def run(*arguments):
        process = await asyncio.create_subprocess_exec(
            *ssh, *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        async with asyncio.timeout(20):
            out, err = await process.communicate()
        return process.returncode, out, err

    try:
        timings = []
        for number in range(4):
            started = time.monotonic()
            code, out, err = await run("fixture@127.0.0.1", f"printf {number}")
            timings.append(time.monotonic() - started)
            assert code == 0, err
            assert out == str(number).encode()
        # One TCP connection and one admission carried every command; a second
        # sign-in would have been refused by this credential.
        assert Counted.made == 1
        assert service.admitted == 1
        assert service.opened == 4
        assert max(timings[1:]) < 2
    finally:
        await run("-O", "exit", "fixture@127.0.0.1")
        gateway.close()
        await gateway.wait_closed()


async def connect_proxy(required_auth):
    """A minimal HTTP CONNECT proxy, as an Agent's sandbox puts in front of the internet."""
    seen = []

    async def pipe(reader, writer):
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    async def handle(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        seen.append(head)
        if required_auth not in head:
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        host, port = head.split(b"\r\n")[0].split()[1].decode().rsplit(":", 1)
        upstream_reader, upstream_writer = await asyncio.open_connection(host, int(port))
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            pipe(reader, upstream_writer), pipe(upstream_reader, writer), return_exceptions=True
        )

    return await asyncio.start_server(handle, "127.0.0.1", 0), seen


@pytest.mark.skipif(shutil.which("ssh") is None, reason="needs the OpenSSH client")
async def test_an_agent_behind_an_http_proxy_connects_with_the_helper_from_the_page(tmp_path):
    host = asyncssh.generate_private_key("ssh-ed25519")
    key = asyncssh.generate_private_key("ssh-ed25519")
    service = NativeAccess(key, tmp_path)
    gateway = await start_gateway(service, host, host="127.0.0.1", port=0)
    port = gateway.get_port()
    proxy, seen = await connect_proxy(
        b"Proxy-Authorization: Basic " + base64.b64encode(b"agent:p@ss")
    )
    proxy_port = proxy.sockets[0].getsockname()[1]
    identity = tmp_path / "id"
    key.write_private_key(str(identity))
    os.chmod(identity, 0o600)
    known = tmp_path / "known_hosts"
    known.write_text(f"[127.0.0.1]:{port} " + host.export_public_key().decode())
    helper = tmp_path / "puppyone_proxy.py"
    helper.write_text(PROXY_HELPER)
    ssh = [
        "ssh",
        "-F",
        "/dev/null",
        "-i",
        str(identity),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        f"UserKnownHostsFile={known}",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ProxyCommand={sys.executable} {helper} %h %p",
        "-p",
        str(port),
    ]

    async def run(proxy_url):
        process = await asyncio.create_subprocess_exec(
            *ssh,
            "fixture@127.0.0.1",
            "printf through-the-proxy",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "HTTPS_PROXY": proxy_url},
        )
        async with asyncio.timeout(20):
            out, err = await process.communicate()
        return process.returncode, out, err

    try:
        # A refusal is said in the proxy's own words, not as an SSH fault.
        code, _, err = await run(f"http://127.0.0.1:{proxy_port}")
        assert code != 0 and b"The proxy refused the connection: HTTP/1.1 407" in err
        code, out, err = await run(f"http://agent:p%40ss@127.0.0.1:{proxy_port}")
        assert code == 0, err
        assert out == b"through-the-proxy"
        assert seen[-1].startswith(f"CONNECT 127.0.0.1:{port} HTTP/1.1".encode())
    finally:
        proxy.close()
        await proxy.wait_closed()
        gateway.close()
        await gateway.wait_closed()
