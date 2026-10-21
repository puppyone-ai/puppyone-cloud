"""App-scoped flyctl agent shared by SSH channels in one backend event loop.

Own the agent's stdin so backend death closes its private tunnel. Never connect
through the workstation's default agent/config or persist the bearer token.
"""

import asyncio
import hashlib
import os
import tempfile
import time
import weakref
from contextlib import suppress
from pathlib import Path

from ..worker_port import WorkerDisconnected

_pools = weakref.WeakKeyDictionary()


class FlyTransport:
    def __init__(self, token, cli):
        self.directory = tempfile.TemporaryDirectory(prefix="puppy-fly-", dir="/tmp")
        root = Path(self.directory.name)
        (root / "config.yml").write_text("{}\n")
        (root / "config.yml").chmod(0o600)
        # `fly agent run <log>` opens an existing file without O_CREAT.
        (root / "agent.log").touch(mode=0o600)
        self.env = {
            "FLY_CONFIG_DIR": str(root),
            "FLY_AGENT_SOCKET_PATH": str(root / "agent.sock"),
            "FLY_AGENT_ISOLATED": "1",
            "FLY_WIREGUARD_TOKEN_MODE": "1",
            "FLY_NO_UPDATE_CHECK": "1",
            "FLY_API_TOKEN": token,
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(root),
        }
        self.cli, self.process = cli, None
        self.lock = asyncio.Lock()
        self.users, self.idle_task = 0, None

    async def acquire(self):
        async with self.lock:
            if self.idle_task:
                self.idle_task.cancel()
                self.idle_task = None
            if self.process is None or self.process.returncode is not None:
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
            self.users += 1
        return self

    def release(self):
        self.users -= 1
        if self.users == 0:
            self.idle_task = asyncio.create_task(self._idle())

    async def _idle(self):
        await asyncio.sleep(300)
        async with self.lock:
            if self.users == 0:
                await self.close()

    async def close(self):
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
                        old.directory.cleanup()
                        entries.pop(old_key)
                        break
                else:
                    raise WorkerDisconnected("Fly private transport capacity reached")
            entries[key] = FlyTransport(token, cli)
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
        transport.directory.cleanup()
