"""App-scoped flyctl agent shared by SSH channels in one backend event loop.

Borrow a verified supervisor-owned agent when configured, otherwise own its stdin
so backend death closes the private tunnel. Never use default workstation config
or persist the bearer token.
"""

import asyncio
import hashlib
import time
import weakref
from contextlib import suppress
from pathlib import Path

from ..timing import workspace_stage
from ..worker_port import WorkerDisconnected
from .fly_agent import create_directory, isolated_environment, managed_environment

_pools = weakref.WeakKeyDictionary()


class FlyTransport:
    def __init__(self, token, cli, app=""):
        self.app, self.token = app, token
        self.env = managed_environment(app, token, cli)
        self.managed = self.env is not None
        self.directory = None if self.managed else create_directory()
        if not self.managed:
            self.env = isolated_environment(Path(self.directory.name), token)
        self.cli, self.process = cli, None
        self.lock = asyncio.Lock()
        self.users, self.idle_task = 0, None
        self.ssh = None

    def ssh_connections(self):
        if self.ssh is None:
            from src.config import settings

            from .fly_ssh import FlySSHConnections

            self.ssh = FlySSHConnections(
                self.app,
                self.token,
                self.cli,
                self.env,
                limit=settings.CLOUD_AGENT_FILE_CHANNEL_LIMIT,
                idle_seconds=settings.CLOUD_AGENT_FILE_CHANNEL_IDLE_SECONDS,
            )
        return self.ssh

    async def acquire(self):
        async with self.lock:
            if self.managed:
                # Fail closed on owner/configuration loss. Do not attach to a
                # default user agent or silently replace the supervisor agent.
                self.env = managed_environment(self.app, self.token, self.cli)
                if self.env is None:
                    raise WorkerDisconnected("Managed Fly agent is unavailable")
                self.users += 1
                return self
            if self.idle_task:
                self.idle_task.cancel()
                self.idle_task = None
            if self.process is None or self.process.returncode is not None:
                with workspace_stage("connection.agent_start"):
                    await self._start()
            self.users += 1
        return self

    async def _start(self):
        # Only remove this manager's stale socket after its process exits.
        Path(self.env["FLY_AGENT_SOCKET_PATH"]).unlink(missing_ok=True)
        self.process = await asyncio.create_subprocess_exec(
            self.cli,
            "agent",
            "run",
            str(Path(self.directory.name) / "agent.log"),
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            started = time.monotonic()
            while not Path(self.env["FLY_AGENT_SOCKET_PATH"]).exists():
                if self.process.returncode is not None or time.monotonic() - started > 10:
                    raise WorkerDisconnected("Fly private transport failed to start")
                await asyncio.sleep(0.02)
        except BaseException:
            # A cancelled first borrower must not orphan its tunnel.
            await self.close()
            raise

    def release(self):
        self.users -= 1
        if self.users == 0 and not self.managed:
            self.idle_task = asyncio.create_task(self._idle())

    async def _idle(self):
        await asyncio.sleep(300)
        async with self.lock:
            if self.users == 0:
                await self.close()

    async def close(self):
        if self.ssh is not None:
            await self.ssh.close()
            self.ssh = None
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                async with asyncio.timeout(5):
                    await self.process.wait()
            except TimeoutError:
                with suppress(ProcessLookupError):
                    self.process.kill()
                await self.process.wait()
            self.process = None


async def acquire_transport(app, token, cli):
    pool = _pools.setdefault(asyncio.get_running_loop(), ({}, asyncio.Lock()))
    entries, lock = pool
    key = (app, hashlib.sha256(token.encode()).digest(), cli)
    # Reserve a borrower before another app can evict this configuration. This
    # lock covers local agent startup only, never SSH or provider network I/O.
    async with lock:
        if key not in entries:
            if len(entries) >= 8:
                for old_key, old in list(entries.items()):
                    if not old.users:
                        if old.idle_task:
                            old.idle_task.cancel()
                        await old.close()
                        if old.directory:
                            old.directory.cleanup()
                        entries.pop(old_key)
                        break
                else:
                    raise WorkerDisconnected("Fly private transport capacity reached")
            entries[key] = FlyTransport(token, cli, app)
        return await entries[key].acquire()


async def close_transports():
    pool = _pools.pop(asyncio.get_running_loop(), None)
    if pool is None:
        return
    entries, lock = pool
    async with lock:
        await _close_entries(entries)


async def _close_entries(entries):
    for transport in entries.values():
        if transport.idle_task:
            transport.idle_task.cancel()
        await transport.close()
        if transport.directory:
            transport.directory.cleanup()
