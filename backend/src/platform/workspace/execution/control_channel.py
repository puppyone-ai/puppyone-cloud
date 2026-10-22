"""Provider-neutral framed requests and events shared by Pi and workspace services.

The transport owns no Run or workspace lifecycle. Providers keep their handles;
callers bind the resource before opening a control connection.
"""

import asyncio
import json
import time
from contextlib import suppress
from uuid import uuid4

from src.config import settings
from src.infra.supabase.instrumentation import database_stage

from .providers.port import SEND_TIMEOUT
from .providers.registry import runtime_provider
from .worker_port import WorkerDisconnected

MAX_FRAME = 192 * 1024 * 1024


class ControlChannel:
    def __init__(
        self,
        execution_id,
        project_id,
        *,
        store=None,
        provider=None,
        image=None,
        artifact=None,
        adapter=None,
    ):
        self.execution_id, self.project_id = execution_id, project_id
        self.runtime = adapter or runtime_provider(provider)
        self.provider = self.runtime.name
        self.image = image or settings.CLOUD_AGENT_IMAGE
        self.artifact = artifact or self.runtime.artifact(self.image)
        self.reused = False
        self.parked = False
        self.timeout_renewal = time.monotonic()
        self.queue = asyncio.Queue(maxsize=32)
        self.buffer = ""
        self.process = None
        self.sandbox = None
        self.connection = None
        self.handle = None
        self.reader = None
        self.stderr_reader = None
        self.resource = {
            "provider": self.provider,
            "resource_id": "puppyone-agent-" + execution_id,
            "session_id": execution_id,
            "project_id": project_id,
            "allocation_pending": True,
        }
        self.send_lock = asyncio.Lock()
        self.controls = {}
        self.git_handler = None
        self.git_tasks = set()
        self.recovery = None
        self.restore_point = None
        self.reconnecting = False
        self.output_sequence = 0
        self.runtime.initialize(self)

    async def _disconnect_output(self):
        """Close the SDK subscription, not just the task awaiting its result.

        In E2B a cancelled wait does not close the underlying RPC generator.
        The previous subscription must release that stream before reconnecting.
        """
        await self.runtime.disconnect(self)
        if self.reader:
            self.reader.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.reader

    @property
    def buffer(self):
        return "".join(self._chunks)

    @buffer.setter
    def buffer(self, value):
        # Reconnection discards only the incomplete frame. Accumulating chunks
        # avoids copying a large Git pack for every small transport read.
        self._chunks = [value] if value else []
        self._buffer_bytes = len(value.encode())

    async def _output(self, chunk):
        parts = chunk.split("\n")
        for index, part in enumerate(parts):
            self._buffer_bytes += len(part.encode())
            if self._buffer_bytes > MAX_FRAME:
                raise RuntimeError("Pi control frame too large")
            if part:
                self._chunks.append(part)
            if index == len(parts) - 1:
                break
            line = "".join(self._chunks)
            self.buffer = ""
            if line:
                frame = json.loads(line)
                sequence = frame.get("sequence", 0)
                if type(sequence) is not int or sequence < 1:
                    raise RuntimeError(
                        "Unsupported sandbox control protocol; rebuild worker artifact"
                    )
                if sequence <= self.output_sequence:
                    continue
                if sequence != self.output_sequence + 1:
                    raise WorkerDisconnected("Sandbox output sequence has a gap")
                self.output_sequence = sequence
                if frame.get("type") == "control_result":
                    future = self.controls.pop(frame["id"], None)
                    if future and not future.done():
                        if frame.get("error"):
                            future.set_exception(RuntimeError(frame["error"]))
                        else:
                            future.set_result(frame["result"])
                elif frame.get("type") == "git_http":
                    task = asyncio.create_task(self._git_exchange(frame))
                    self.git_tasks.add(task)
                    task.add_done_callback(self.git_tasks.discard)
                else:
                    await self._event(frame)

    async def _event(self, frame):
        await self.queue.put(frame)

    async def _git_exchange(self, frame):
        try:
            if self.git_handler is None:
                raise PermissionError("No admitted Git transport")
            result = await self.git_handler(frame)
            await self.send({"type": "reply", "id": frame["id"], **result})
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception("agent_git_exchange_failed")
            if isinstance(exc, WorkerDisconnected):
                await self._disconnected()
                await self.runtime.disconnect(self)
            else:
                with suppress(Exception):
                    await self.send(
                        {"type": "reply", "id": frame["id"], "error": type(exc).__name__}
                    )

    async def _disconnected(self):
        if self.reconnecting:
            return
        for future in self.controls.values():
            if not future.done():
                future.set_exception(WorkerDisconnected("Sandbox control connection closed"))
        await self._event({"type": "disconnected"})

    async def control(self, action, value=None):
        phase = {"prepare": "git_prepare", "finalize": "git_commit", "push": "git_push"}.get(
            action, "sandbox_control"
        )
        with database_stage(phase):
            return await self._control(action, value)

    async def _control(self, action, value):
        if self.reader and self.reader.done() and not self.reconnecting:
            raise WorkerDisconnected("Sandbox control connection closed")
        identity = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.controls[identity] = future
        try:
            async with asyncio.timeout(180):
                await self.send(
                    {"type": "control", "id": identity, "action": action, "value": value}
                )
                return await future
        finally:
            self.controls.pop(identity, None)

    async def send(self, value):
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        if len(data.encode()) > MAX_FRAME:
            raise ValueError("Pi input frame too large")
        megabytes = len(data.encode()) / (1024 * 1024)
        timeout = min(120, SEND_TIMEOUT + megabytes) if megabytes > 1 else SEND_TIMEOUT
        try:
            async with asyncio.timeout(timeout), self.send_lock:
                await self.runtime.send(self, data, timeout=timeout)
        except TimeoutError as exc:
            raise WorkerDisconnected("Sandbox input delivery timed out") from exc

    async def receive(self):
        return await self.queue.get()
