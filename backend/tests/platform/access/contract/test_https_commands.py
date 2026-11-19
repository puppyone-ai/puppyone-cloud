"""An Agent runs commands over HTTPS with the helper its page gives it.

The endpoint is served over real TLS and reached through an HTTP CONNECT proxy,
as from a sandbox whose only way out is a proxy; the Workspace shell is the real
helper run locally, as in the SSH transport tests.
"""

import asyncio
import datetime
import ipaddress
import os
import shutil
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI

from src.config import settings
from src.platform.access import agent_link_router as routes
from src.platform.access.adapters import shell_session
from src.platform.access.adapters.https.commands import AgentCommands, SeenSignatures
from src.platform.access.adapters.https.signature import command_message
from src.platform.access.agent_links import RUN_HELPER, https_access
from src.platform.workspace.errors import WorkspaceError
from tests.support.native_shell import NativeAccess

pytestmark = pytest.mark.skipif(
    not (shutil.which("ssh-keygen") and shutil.which("curl")), reason="needs ssh-keygen and curl"
)


class Access(NativeAccess):
    """Every HTTPS request is a connection of its own, admitted as SSH admits one."""

    def __init__(self, directory):
        super().__init__(None, directory)
        self.identities = []

    async def admit(self, identity, notify=None):
        self.admitted += 1
        self.identities.append(identity)
        if notify:
            await notify("Preparing your workspace; this can take a few minutes.\r\n")
        return {"id": identity}


def certificate(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = directory / "cert.pem", directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


async def connect_proxy():
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
        host, port = head.split(b"\r\n")[0].split()[1].decode().rsplit(":", 1)
        upstream_reader, upstream_writer = await asyncio.open_connection(host, int(port))
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            pipe(reader, upstream_writer), pipe(upstream_reader, writer), return_exceptions=True
        )

    return await asyncio.start_server(handle, "127.0.0.1", 0), seen


class Agent:
    """The Agent's computer: its key, the helper from its page, and a proxy-only network."""

    def __init__(self, directory, origin, connection, cert, proxy_port):
        self.directory, self.origin, self.connection = directory, origin, connection
        (directory / ".ssh").mkdir(mode=0o700)
        self.key = directory / ".ssh/puppyone_agent"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key)], check=True
        )
        self.public_key = " ".join(self.key.with_suffix(".pub").read_text().split()[:2])
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(directory),
            "CURL_CA_BUNDLE": str(cert),
            "https_proxy": f"http://127.0.0.1:{proxy_port}",
            "NO_PROXY": "",
        }
        self.run_path = directory / ".puppyone/run"

    def install(self, access):
        """The two things the page asks for, as written: ``~`` is this computer's home."""
        subprocess.run(["sh", "-c", access["install"]], check=True, env=self.env)
        self.run_path.write_text(RUN_HELPER)
        self.run_path.chmod(0o700)

    async def run(self, command):
        process = await asyncio.create_subprocess_exec(
            str(self.run_path),
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        async with asyncio.timeout(30):
            out, err = await process.communicate()
        return process.returncode, out.decode(), err.decode()

    def sign(self, message):
        target = self.directory / f"message-{uuid4().hex}"
        target.write_bytes(message)
        subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", str(self.key), "-n", "puppyone-agent", str(target)],
            check=True,
            capture_output=True,
        )
        armored = target.with_name(target.name + ".sig").read_text().splitlines()
        return "".join(armored[1:-1])


@asynccontextmanager
async def serving(tmp_path, monkeypatch, *, admits=None):
    connection = str(uuid4())
    service = Access(tmp_path / "workspace")
    service.directory.mkdir()
    cert, key = certificate(tmp_path)
    proxy, seen = await connect_proxy()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"https://127.0.0.1:{listener.getsockname()[1]}"
    agent = Agent(agent_dir, origin, connection, cert, proxy.sockets[0].getsockname()[1])

    async def approved(identity, presented):
        return identity == connection and presented == agent.public_key

    commands = AgentCommands(service, seen=SeenSignatures(url=""), admits=admits or approved)
    monkeypatch.setattr(settings, "ACCESS_LIFECYCLE_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_URL", origin)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.agent_commands] = lambda: commands
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="error",
            access_log=False,
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.02)
        async with httpx.AsyncClient(base_url=origin, verify=str(cert), timeout=20) as client:
            yield agent, service, commands, client, seen
    finally:
        server.should_exit = True
        await task
        proxy.close()
        await proxy.wait_closed()


async def test_the_helper_from_the_page_runs_commands_through_a_proxy(tmp_path, monkeypatch):
    async with serving(tmp_path, monkeypatch) as (agent, service, _, _, seen):
        agent.install(https_access(agent.connection))
        code, out, _ = await agent.run("pwd && printf 'written' > agent.txt && echo done")
        assert code == 0
        lines = out.splitlines()
        assert lines[0] == "PuppyOne: Preparing your workspace; this can take a few minutes."
        assert lines[-2:] == ["done", "[exit 0]"]
        assert (service.directory / "agent.txt").read_text() == "written"
        # The command's own status is the helper's, and stderr arrives with stdout.
        code, out, _ = await agent.run("echo oops >&2; sleep 0.3; printf 'no newline'; exit 3")
        assert code == 3
        assert out.endswith("oops\nno newline\n[exit 3]\n")
        # Every command went out through the proxy, as the only way out.
        assert len(seen) == 2
        assert all(head.startswith(b"CONNECT 127.0.0.1:") for head in seen)
        assert service.identities == [f"agent-{agent.connection}"] * 2
        assert service.finished == service.identities
        assert service.closed


async def test_only_a_fresh_request_signed_by_the_approved_key_runs(tmp_path, monkeypatch):
    async with serving(tmp_path, monkeypatch) as (agent, service, _, client, _):
        path = f"/api/v1/agent-links/connections/{agent.connection}/exec"

        async def post(command, *, signed=None, moment=None, signature=None):
            moment = int(time.time()) if moment is None else moment
            signed = command if signed is None else signed
            signature = signature or agent.sign(command_message(agent.connection, moment, signed))
            return await client.post(
                path,
                content=command,
                headers={"X-PuppyOne-Time": str(moment), "X-PuppyOne-Signature": signature},
            )

        good = await post(b"echo ok")
        assert good.status_code == 200
        assert good.text.endswith("ok\n[exit 0]\n")

        tampered = await post(b"rm -rf .", signed=b"echo ok")
        assert tampered.status_code == 401
        assert tampered.headers["x-puppyone-code"] == "agent_signature_invalid"
        assert tampered.text.startswith("PuppyOne: ")

        stale = await post(b"echo ok", moment=int(time.time()) - 600)
        assert stale.headers["x-puppyone-code"] == "agent_clock_skew"

        moment = int(time.time())
        signature = agent.sign(command_message(agent.connection, moment, b"echo once"))
        assert (await post(b"echo once", moment=moment, signature=signature)).status_code == 200
        replayed = await post(b"echo once", moment=moment, signature=signature)
        assert replayed.status_code == 409
        assert replayed.headers["x-puppyone-code"] == "agent_signature_replayed"

        other = tmp_path / "other"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True
        )
        agent.key = other
        stranger = await post(b"echo ok")
        assert stranger.status_code == 403
        assert stranger.headers["x-puppyone-code"] == "access_denied"

        # A signature made for anything else, even by this key, is not a command's.
        moment = int(time.time())
        message = agent.directory / "elsewhere"
        message.write_bytes(command_message(agent.connection, moment, b"echo ok"))
        subprocess.run(
            ["ssh-keygen", "-Y", "sign", "-f", str(agent.key), "-n", "file", str(message)],
            check=True,
            capture_output=True,
        )
        other_use = "".join(message.with_name("elsewhere.sig").read_text().splitlines()[1:-1])
        refused = await post(b"echo ok", moment=moment, signature=other_use)
        assert refused.headers["x-puppyone-code"] == "agent_signature_invalid"

        unsigned = await client.post(path, content=b"echo ok")
        assert unsigned.status_code == 401

        too_long = await client.post(path, content=b"x" * 40000, headers={"X-PuppyOne-Time": "0"})
        assert too_long.status_code == 413
        # Nothing refused reached the Workspace.
        assert service.admitted == 2


async def test_a_session_ended_by_the_owner_says_why(tmp_path, monkeypatch):
    async with serving(tmp_path, monkeypatch) as (agent, service, _, _, _):
        agent.install(https_access(agent.connection))
        service.renewal_error = WorkspaceError(403, {"code": "access_denied"})
        monkeypatch.setattr(shell_session, "HEARTBEAT_SECONDS", 0.05)
        code, out, _ = await agent.run("sleep 5; echo never")
        assert code == 1
        assert "PuppyOne: access to this workspace is not available" in out
        assert out.endswith("[exit 1]\n")
        assert "never" not in out
        assert service.closed


async def test_a_client_that_leaves_stops_its_command(tmp_path, monkeypatch):
    async with serving(tmp_path, monkeypatch) as (agent, service, commands, client, _):
        moment = int(time.time())
        command = b"sleep 30"
        signature = agent.sign(command_message(agent.connection, moment, command))
        headers = {"X-PuppyOne-Time": str(moment), "X-PuppyOne-Signature": signature}
        async with client.stream(
            "POST",
            f"/api/v1/agent-links/connections/{agent.connection}/exec",
            content=command,
            headers=headers,
        ) as response:
            assert response.status_code == 200
            async for _ in response.aiter_bytes():
                break  # The preparing notice: the command is running.
        async with asyncio.timeout(10):
            while not service.closed or commands.running:
                await asyncio.sleep(0.05)
        assert service.finished == [f"agent-{agent.connection}"]


async def test_a_client_that_leaves_while_its_machine_is_prepared_runs_nothing(
    tmp_path, monkeypatch
):
    async with serving(tmp_path, monkeypatch) as (agent, service, commands, client, _):
        preparing = asyncio.Event()
        admit = service.admit

        async def slow_admit(identity, notify=None):
            session = await admit(identity, notify)
            await preparing.wait()
            return session

        service.admit = slow_admit
        moment = int(time.time())
        command = b"touch ran"
        signature = agent.sign(command_message(agent.connection, moment, command))
        headers = {"X-PuppyOne-Time": str(moment), "X-PuppyOne-Signature": signature}
        async with client.stream(
            "POST",
            f"/api/v1/agent-links/connections/{agent.connection}/exec",
            content=command,
            headers=headers,
        ) as response:
            async for _ in response.aiter_bytes():
                break
        await asyncio.sleep(0.3)
        preparing.set()
        async with asyncio.timeout(10):
            while commands.running:
                await asyncio.sleep(0.05)
        assert not (service.directory / "ran").exists()
        assert service.opened == 0
        assert service.finished == [f"agent-{agent.connection}"]


async def test_a_command_arrives_byte_for_byte_and_the_shell_reads_it(tmp_path, monkeypatch):
    async with serving(tmp_path, monkeypatch) as (agent, service, _, _, _):
        agent.install(https_access(agent.connection))
        # Quotes, a non-ASCII word, a literal $HOME and an expansion the workspace makes.
        code, out, _ = await agent.run(
            "printf '%s|' \"it's\" 'héllo 你好' '$HOME' \"$((1+1))\"; printf 'end\\n'"
        )
        assert code == 0
        assert "it's|héllo 你好|$HOME|2|end\n[exit 0]\n" in out
        # A multi-line command with a heredoc: the way to give a command its input.
        code, out, _ = await agent.run(
            "cat > note.txt <<'EOF'\nline one\n  indented \\ backslash\nEOF\ncat note.txt"
        )
        assert code == 0
        assert (service.directory / "note.txt").read_text() == "line one\n  indented \\ backslash\n"
        assert out.endswith("line one\n  indented \\ backslash\n[exit 0]\n")


async def test_a_large_output_streams_whole(tmp_path, monkeypatch):
    async with serving(tmp_path, monkeypatch) as (agent, _, _, _, _):
        agent.install(https_access(agent.connection))
        code, out, _ = await agent.run("head -c 2000000 /dev/zero | tr '\\0' x")
        assert code == 0
        notice, body, marker, end = out.split("\n")
        assert notice.startswith("PuppyOne: ") and (marker, end) == ("[exit 0]", "")
        assert body == "x" * 2_000_000


async def test_the_helper_says_what_is_missing(tmp_path, monkeypatch):
    async with serving(tmp_path, monkeypatch) as (agent, _, _, _, _):
        agent.run_path.parent.mkdir(exist_ok=True)
        agent.run_path.write_text(RUN_HELPER)
        agent.run_path.chmod(0o700)

        async def helper(*arguments):
            process = await asyncio.create_subprocess_exec(
                str(agent.run_path),
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=agent.env,
            )
            out, err = await process.communicate()
            return process.returncode, out.decode(), err.decode()

        code, _, err = await helper()
        assert code == 2 and "usage:" in err
        code, _, err = await helper("one", "two")
        assert code == 2 and "usage:" in err
        # Before https.install, it says to run it.
        code, _, err = await helper("pwd")
        assert code == 2 and "run https.install first" in err
        agent.install(https_access(agent.connection))
        # Without its key it cannot sign, and says why.
        agent.key.rename(agent.key.with_name("moved"))
        code, out, err = await helper("pwd")
        assert code == 1 and out == "" and err


async def test_a_refused_request_tells_the_agent_and_fails_the_helper(tmp_path, monkeypatch):
    async def nobody(identity, presented):
        return False

    async with serving(tmp_path, monkeypatch, admits=nobody) as (agent, service, _, client, _):
        agent.install(https_access(agent.connection))
        code, out, _ = await agent.run("pwd")
        assert code == 1
        assert out.startswith("PuppyOne: access to this workspace is not available")
        assert service.admitted == 0
        moment = int(time.time())
        signature = agent.sign(command_message(agent.connection, moment, b"pwd"))
        refused = await client.post(
            f"/api/v1/agent-links/connections/{agent.connection}/exec",
            content=b"pwd",
            headers={"X-PuppyOne-Time": str(moment), "X-PuppyOne-Signature": signature},
        )
        assert refused.status_code == 403
        assert refused.headers["content-type"].startswith("text/plain")
        assert refused.headers["cache-control"] == "no-store"
        assert refused.headers["x-puppyone-code"] == "access_denied"


async def test_the_response_is_plain_streamed_text_never_cached(tmp_path, monkeypatch):
    async with serving(tmp_path, monkeypatch) as (agent, _, _, client, _):
        moment = int(time.time())
        signature = agent.sign(command_message(agent.connection, moment, b"echo ok"))
        response = await client.post(
            f"/api/v1/agent-links/connections/{agent.connection}/exec",
            content=b"echo ok",
            headers={"X-PuppyOne-Time": str(moment), "X-PuppyOne-Signature": signature},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers.get("transfer-encoding") == "chunked"


async def test_no_command_runs_while_access_lifecycle_is_off(tmp_path, monkeypatch):
    async with serving(tmp_path, monkeypatch) as (agent, service, _, client, _):
        monkeypatch.setattr(settings, "ACCESS_LIFECYCLE_ENABLED", False)
        moment = int(time.time())
        signature = agent.sign(command_message(agent.connection, moment, b"echo ok"))
        response = await client.post(
            f"/api/v1/agent-links/connections/{agent.connection}/exec",
            content=b"echo ok",
            headers={"X-PuppyOne-Time": str(moment), "X-PuppyOne-Signature": signature},
        )
        assert response.status_code == 503
        assert service.admitted == 0
