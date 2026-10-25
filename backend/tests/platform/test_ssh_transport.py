"""Real SSH transport against the native PTY helper; provider lifecycle tested separately."""

import asyncio
import base64
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import asyncssh
import pytest

from src.platform.access.adapters.ssh.gateway import start_gateway

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

    async def authenticate(self, identity, key):
        return (
            identity == "fixture"
            and key.export_public_key() == self.key.export_public_key()
            and not self.used
        )

    @asynccontextmanager
    async def open(self, identity, notify=None):
        if self.used:
            raise PermissionError("Used")
        self.used = True
        if getattr(self, "resume_notice", None):
            await notify(self.resume_notice)
        shell = NativeShell(self.directory)
        try:
            yield {"id": identity}, shell
        finally:
            await shell.close()
            self.closed = True

    async def renew(self, *args):
        return None


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
