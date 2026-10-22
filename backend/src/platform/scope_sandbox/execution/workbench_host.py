"""Lifecycle and file channels borrow one persisted provider resource.

They share framing with Pi but never launch a Loop. Requests and subscriptions
borrow one service connection; releasing a borrower never pauses compute.
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from uuid import UUID

from .control_channel import ControlChannel
from .runtime_contract import validate_runtime
from .worker_port import WorkerDisconnected


class WorkbenchChannel(ControlChannel):
    def __init__(self, row, *, purpose="lifecycle", store=None):
        resource = row["resource"] or {}
        binding = row["binding"]
        super().__init__(
            str(UUID(row["generation"])),
            row["project_id"],
            store=store,
            provider=binding["provider"],
            image=resource.get("image_id", binding["artifact"]),
            artifact=resource.get("template", resource.get("image_id", binding["artifact"])),
        )
        if purpose not in {"lifecycle", "files"}:
            raise ValueError("Invalid workspace channel purpose")
        self.purpose = purpose
        self.workbench_id, self.generation = str(UUID(row["id"])), str(UUID(row["generation"]))
        self.resource.update(
            workbench_id=self.workbench_id,
            workbench_generation=self.generation,
            workspace_id=self.workbench_id,
        )
        self.resource.update(resource)
        self.compute_epoch = row.get("compute_epoch")
        self.attached = True
        self.subscribers = set()
        self.watch_lock = asyncio.Lock()
        self.watch_started = False
        self.service_receipt = None

    async def create(self):
        await self.runtime.allocate(self)
        self.resource["allocation_pending"] = False
        return dict(self.resource)

    async def _open_control(self):
        await self.runtime.open_channel(self)

    def control_command(self):
        if self.purpose == "files":
            return ["node", "/opt/puppyone-agent/workbench-file-client.mjs"]
        return [
            "node",
            f"/opt/puppyone-agent/workbench-{self.purpose}.mjs",
            self.workbench_id,
            self.generation,
        ]

    def attached_control_command(self):
        return self.control_command()

    async def connect(self, connection=None):
        connection = connection or await self.runtime.running_connection(self)
        await self.runtime.connect_task(self, self.resource, connection)
        if self.purpose == "files":
            await self.runtime.open_service(self, "files")
            receipt = await self.control(
                "hello",
                {
                    "protocol": 1,
                    "workbench": self.workbench_id,
                    "generation": self.generation,
                    "compute_epoch": self.compute_epoch,
                },
            )
            expected = {
                "protocol": 1,
                "workbench": self.workbench_id,
                "generation": self.generation,
                "compute_epoch": self.compute_epoch,
            }
            if any(receipt.get(key) != value for key, value in expected.items()):
                raise WorkerDisconnected("Workspace service binding mismatch")
            try:
                for key in ("boot_id", "service_id"):
                    UUID(receipt[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise WorkerDisconnected("Invalid workspace service receipt") from exc
            validate_runtime(receipt.get("runtime"))
            self.service_receipt = receipt
        else:
            await self._open_control()
        return self

    async def _event(self, frame):
        if self.purpose != "files":
            return await super()._event(frame)
        for queue in tuple(self.subscribers):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                event = (
                    frame
                    if frame.get("type") == "disconnected"
                    else {
                        "type": "workspace_changed",
                        "generation": self.generation,
                        "reset": True,
                    }
                )
            else:
                event = frame
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self):
        queue = asyncio.Queue(maxsize=32)
        self.subscribers.add(queue)
        try:
            async with self.watch_lock:
                if not self.watch_started:
                    await self.control("watch")
                    self.watch_started = True
            yield queue
        finally:
            self.subscribers.discard(queue)
            async with self.watch_lock:
                if not self.subscribers and self.watch_started and self.healthy():
                    with suppress(Exception):
                        async with asyncio.timeout(5):
                            await self.control("unwatch")
                    self.watch_started = False

    def healthy(self):
        return bool(self.reader and not self.reader.done() and not self.reconnecting)

    async def close(self):
        await self._disconnected()
        self.reconnecting = True
        try:
            await self._disconnect_output()
        finally:
            try:
                await self.runtime.close_channel(self)
            finally:
                for task in (*self.git_tasks, self.reader, self.stderr_reader):
                    if task:
                        task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await task


@dataclass
class _FileChannel:
    workspace: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    channel: object = None
    borrowers: int = 0
    used: float = field(default_factory=time.monotonic)
    retired: bool = False


class WorkbenchChannels:
    """Bounded local transport cache. DB admission remains authoritative.

    Only connection establishment serializes; requests and watchers share the
    framed transport. A failed mutation is never replayed. Cached sockets are neither
    workspace ownership nor activity and never create/resume compute.
    """

    def __init__(self, factory=WorkbenchChannel, *, idle_seconds=30, limit=128):
        self.factory, self.idle_seconds, self.limit = factory, idle_seconds, limit
        self.entries = {}
        self.sweeper = None
        self.closed = False

    @staticmethod
    def key(row):
        return json.dumps(
            [
                row["id"],
                row["generation"],
                row.get("compute_epoch"),
                row["binding"],
                row["resource"],
            ],
            sort_keys=True,
        )

    async def _discard(self, entry):
        channel, entry.channel = entry.channel, None
        if channel:
            await channel.close()

    async def _sweep(self):
        while True:
            await asyncio.sleep(min(self.idle_seconds, 5))
            for key, entry in list(self.entries.items()):
                if not entry.borrowers and time.monotonic() - entry.used >= self.idle_seconds:
                    self.entries.pop(key, None)
                    with suppress(Exception):
                        await self._discard(entry)

    @asynccontextmanager
    async def borrow(self, row):
        if self.closed:
            raise WorkerDisconnected("Workspace channel pool is closed")
        key = self.key(row)
        retired = []
        # Reserve capacity and borrower count before the first await. Otherwise
        # concurrent requests could exceed the bound while closing evictions.
        if key not in self.entries:
            for old_key, old in list(self.entries.items()):
                if old.workspace == row["id"]:
                    old.retired = True
                    self.entries.pop(old_key)
                    retired.append(old)
            if len(self.entries) >= self.limit:
                idle = [(k, e) for k, e in self.entries.items() if not e.borrowers]
                if not idle:
                    raise WorkerDisconnected("Workspace channel capacity reached")
                old_key, old = min(idle, key=lambda item: item[1].used)
                old.retired = True
                self.entries.pop(old_key)
                retired.append(old)
            self.entries[key] = _FileChannel(row["id"])
        entry = self.entries[key]
        entry.borrowers += 1
        if self.sweeper is None:
            self.sweeper = asyncio.create_task(self._sweep())
        try:
            for old in retired:
                if not old.borrowers:
                    await self._discard(old)
            async with entry.lock:
                if self.closed or entry.retired:
                    raise WorkerDisconnected("Workspace channel binding was retired")
                if entry.channel and not entry.channel.healthy():
                    await self._discard(entry)
                if entry.channel is None:
                    entry.channel = self.factory(row, purpose="files")
                    try:
                        await entry.channel.connect()
                    except BaseException:
                        await self._discard(entry)
                        raise
                channel = entry.channel
            try:
                yield channel
            except (ConnectionError, OSError):
                async with entry.lock:
                    if entry.channel is channel:
                        await self._discard(entry)
                raise
            finally:
                entry.used = time.monotonic()
        finally:
            entry.borrowers -= 1
            if entry.retired and not entry.borrowers:
                await self._discard(entry)

    async def invalidate(self, workspace):
        for key, entry in list(self.entries.items()):
            if entry.workspace == workspace:
                self.entries.pop(key, None)
                entry.retired = True
                async with entry.lock:
                    await self._discard(entry)

    async def close(self):
        self.closed = True
        if self.sweeper:
            self.sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await self.sweeper
        entries, self.entries = list(self.entries.values()), {}

        async def close_entry(entry):
            entry.retired = True
            async with entry.lock:
                await self._discard(entry)

        results = await asyncio.gather(
            *(close_entry(entry) for entry in entries), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
