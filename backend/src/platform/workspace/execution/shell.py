"""Native shell/PTY control over the same transports as files and Agent tasks."""

import asyncio
from contextlib import suppress
from uuid import UUID

from .control_channel import ControlChannel
from .worker_port import WorkerDisconnected


class WorkspaceShell(ControlChannel):
    def __init__(self, connection, *, adapter=None):
        self.task = connection
        self.identity = str(UUID(connection["id"]))
        self.workbench = str(UUID(connection["workbench_id"]))
        self.generation = str(UUID(connection["generation"]))
        self.task_uid = connection["task_uid"]
        if type(self.task_uid) is not int or not 20000 <= self.task_uid <= 59999:
            raise ValueError("Invalid shell task identity")
        binding, resource = connection["binding"], connection["resource"]
        super().__init__(
            self.identity,
            connection["project_id"],
            provider=binding["provider"],
            image=binding["artifact"],
            artifact=binding["artifact"],
            adapter=adapter,
        )
        if (
            resource.get("task_execution_id") != self.identity
            or resource.get("task_uid") != self.task_uid
            or resource.get("workbench_id") != self.workbench
            or resource.get("workbench_generation") != self.generation
            or resource.get("project_id") != connection["project_id"]
            or resource.get("provider") != binding["provider"]
        ):
            raise PermissionError("Shell resource binding mismatch")
        self.resource = dict(resource)
        self.attached = True
        self.overflow = False

    def control_command(self):
        return [
            "node",
            "/opt/puppyone-agent/workbench-shell.mjs",
            str(self.task_uid),
            self.workbench,
            self.generation,
            self.identity,
        ]

    def attached_control_command(self):
        return self.control_command()

    async def connect(self, connection=None):
        connection = connection or await self.runtime.running_connection(self)
        await self.runtime.connect_task(self, self.resource, connection)
        await self.runtime.open_channel(self)
        async with asyncio.timeout(15):
            receipt = await self.control("capabilities")
        if receipt != {
            "workspace_shell": 1,
            "workbench": self.workbench,
            "generation": self.generation,
            "execution": self.identity,
        }:
            raise WorkerDisconnected("Workspace shell protocol mismatch")

    async def _event(self, frame):
        if self.overflow:
            return
        if self.queue.full():
            self.overflow = True
            while not self.queue.empty():
                self.queue.get_nowait()
            frame = {"type": "disconnected"}
        self.queue.put_nowait(frame)

    async def control(self, action, value=None):
        async with asyncio.timeout(15):
            return await super().control(action, value)

    async def stop(self):
        if not self.reader or self.reader.done():
            await self.close()
            self.reconnecting = False
            self.output_sequence, self.buffer = 0, ""
            await self.connect()
        async with asyncio.timeout(20):
            await self.control("stop")

    async def close(self):
        self.reconnecting = True
        try:
            await self._disconnect_output()
            await self.runtime.close_channel(self)
        finally:
            for future in self.controls.values():
                if not future.done():
                    future.set_exception(WorkerDisconnected("Shell channel closed"))
            for task in (self.reader, self.stderr_reader):
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
            await self.runtime.aclose()
