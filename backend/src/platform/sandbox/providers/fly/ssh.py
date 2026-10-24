"""Bounded SSH sessions over the official Fly private-network proxy.

flyctl owns WireGuard and its protocol. AsyncSSH owns authenticated SSH
connections and channels. Keys/certificates stay in memory, scoped to one app
and backend token. No workstation SSH config, agent or public network listener.
"""

import asyncio
import ipaddress
import os
import stat
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh
import httpx

from src.platform.sandbox.errors import WorkerDisconnected
from src.platform.sandbox.port import resource_identity
from src.platform.sandbox.timing import workspace_stage

from .certificate import import_user_certificate


@dataclass
class _Connection:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ssh: object = None
    proxy: object = None
    directory: object = None
    readers: list = field(default_factory=list)
    users: int = 0
    epoch: int | None = None
    used: float = field(default_factory=time.monotonic)

    async def close_ssh(self):
        if self.ssh:
            self.ssh.close()
            with suppress(Exception):
                await self.ssh.wait_closed()
            self.ssh = None

    async def close(self):
        await self.close_ssh()
        if self.proxy:
            self.proxy.stdin.close()
            try:
                async with asyncio.timeout(3):
                    await self.proxy.wait()
            except TimeoutError:
                with suppress(ProcessLookupError):
                    self.proxy.kill()
                await self.proxy.wait()
            self.proxy = None
        for reader in self.readers:
            reader.cancel()
        await asyncio.gather(*self.readers, return_exceptions=True)
        self.readers.clear()
        if self.directory:
            self.directory.cleanup()
            self.directory = None


class SSHLease:
    def __init__(self, entry, process):
        self.entry, self.process = entry, process

    def release(self):
        if self.entry:
            self.entry.users -= 1
            self.entry.used = time.monotonic()
            self.entry = None


class FlySSHConnections:
    def __init__(self, app, token, cli, env, *, limit=128, idle_seconds=300):
        self.app, self.cli, self.env = app, cli, env
        self.limit, self.idle_seconds = limit, idle_seconds
        self.entries, self.lock, self.credential_lock = {}, asyncio.Lock(), asyncio.Lock()
        self.credential, self.expires = None, 0
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
        self.sweeper = None

    async def _graphql(self, query, variables):
        response = await self.client.post(
            "https://api.fly.io/graphql", json={"query": query, "variables": variables}
        )
        response.raise_for_status()
        value = response.json()
        if value.get("errors") or not value.get("data"):
            # Provider errors may contain credential material; do not echo them.
            raise WorkerDisconnected("Fly SSH credential could not be issued")
        return value["data"]

    async def credentials(self):
        async with self.credential_lock:
            if self.credential and time.monotonic() < self.expires:
                return self.credential
            with workspace_stage("connection.ssh_credential"):
                app = await self._graphql(
                    "query($name:String!){app(name:$name){organization{id}}}", {"name": self.app}
                )
                key = asyncssh.generate_private_key("ssh-ed25519")
                issued = await self._graphql(
                    "mutation($input:IssueCertificateInput!){issueCertificate(input:$input){certificate}}",
                    {
                        "input": {
                            "organizationId": app["app"]["organization"]["id"],
                            "principals": ["root", "fly"],
                            "appNames": [self.app],
                            "validHours": 1,
                            "publicKey": key.export_public_key().decode().strip(),
                        }
                    },
                )
                certificate = import_user_certificate(
                    issued["issueCertificate"]["certificate"], key, self.app
                )
                self.credential = (key, certificate)
                self.expires = time.monotonic() + 3300
                return self.credential

    async def _proxy(self, entry, address):
        if entry.proxy and entry.proxy.returncode is None:
            # The authenticated private route survives a Machine stop/start.
            # Its target is immutable for this pool entry; only SSH reconnects.
            return Path(entry.directory.name) / "socket"
        entry.directory = tempfile.TemporaryDirectory(prefix="pw-ssh-")
        socket_path = Path(entry.directory.name) / "socket"
        with workspace_stage("connection.private_proxy"):
            entry.proxy = await asyncio.create_subprocess_exec(
                self.cli,
                "proxy",
                str(socket_path) + ":22",
                address,
                "--app",
                self.app,
                "--quiet",
                "--watch-stdin",
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async def drain(stream):
                while await stream.read(65536):
                    pass

            entry.readers = [
                asyncio.create_task(drain(stream))
                for stream in (entry.proxy.stdout, entry.proxy.stderr)
            ]
            async with asyncio.timeout(25):
                while not socket_path.exists():
                    if entry.proxy.returncode is not None:
                        raise WorkerDisconnected("Fly private proxy failed to start")
                    await asyncio.sleep(0.02)
            info = socket_path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise WorkerDisconnected("Invalid private Fly proxy socket")
        return socket_path

    async def _connect(self, entry, address):
        # Independent credential issuance and private tunnel preparation overlap.
        async with asyncio.TaskGroup() as group:
            credentials = group.create_task(self.credentials())
            proxy = group.create_task(self._proxy(entry, address))
        import socket

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            await asyncio.get_running_loop().sock_connect(sock, str(proxy.result()))
            with workspace_stage("connection.ssh_handshake"):
                # Fly's own SSH client authenticates the server by its private
                # WireGuard route (it does not distribute SSH host CAs). Preserve
                # that trust boundary: this socket exists in our 0700 directory,
                # and targets only the exact IP returned by authenticated inspect.
                entry.ssh = await asyncssh.connect(
                    address,
                    sock=sock,
                    username="root",
                    client_keys=[credentials.result()],
                    known_hosts=None,
                    config=None,
                    agent_path=None,
                    server_host_key_algs=["ssh-ed25519"],
                    encoding=None,
                    connect_timeout=20,
                    login_timeout=15,
                    keepalive_interval=20,
                    keepalive_count_max=3,
                )
        except BaseException:
            sock.close()
            raise

    async def _reserve(self, resource, address):
        if resource.get("app") != self.app:
            raise PermissionError("Fly SSH application mismatch")
        try:
            valid = ipaddress.ip_address(address) in ipaddress.ip_network("fdaa::/16")
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise PermissionError("Fly SSH requires the inspected private address")
        key = (resource_identity(resource), address)
        async with self.lock:
            if key not in self.entries:
                if len(self.entries) >= self.limit:
                    idle = [(k, e) for k, e in self.entries.items() if not e.users]
                    if not idle:
                        raise WorkerDisconnected("Fly SSH connection capacity reached")
                    old_key, old = min(idle, key=lambda pair: pair[1].used)
                    self.entries.pop(old_key)
                    await old.close()
                self.entries[key] = _Connection()
            entry = self.entries[key]
            entry.users += 1
            if self.sweeper is None:
                self.sweeper = asyncio.create_task(self._sweep())
        return entry

    async def prepare(self, resource, address):
        """Only prepare credentials and route; never SSH into stopped compute."""
        entry = await self._reserve(resource, address)
        try:
            async with entry.lock:
                if entry.proxy and entry.proxy.returncode is not None:
                    await entry.close()
                try:
                    async with asyncio.TaskGroup() as group:
                        group.create_task(self.credentials())
                        group.create_task(self._proxy(entry, address))
                except BaseException:
                    await entry.close()
                    raise
        finally:
            entry.users -= 1
            entry.used = time.monotonic()

    async def open(self, resource, address, command, *, epoch=None):
        entry = await self._reserve(resource, address)
        try:
            async with entry.lock:
                if entry.proxy and entry.proxy.returncode is not None:
                    await entry.close()
                elif entry.ssh and (
                    entry.ssh.is_closed() or (epoch is not None and entry.epoch != epoch)
                ):
                    await entry.close_ssh()
                if entry.ssh is None:
                    try:
                        await self._connect(entry, address)
                    except BaseException:
                        await entry.close()
                        raise
                if epoch is not None:
                    entry.epoch = epoch
                with workspace_stage("connection.ssh_channel"):
                    process = await entry.ssh.create_process(command, encoding=None)
            return SSHLease(entry, process)
        except BaseException:
            entry.users -= 1
            entry.used = time.monotonic()
            raise

    async def _sweep(self):
        while True:
            await asyncio.sleep(min(5, self.idle_seconds))
            async with self.lock:
                for key, entry in list(self.entries.items()):
                    if not entry.users and time.monotonic() - entry.used >= self.idle_seconds:
                        self.entries.pop(key)
                        await entry.close()

    async def close(self):
        if self.sweeper:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
        await asyncio.gather(*(entry.close() for entry in self.entries.values()))
        self.entries.clear()
        self.credential = None
        await self.client.aclose()
