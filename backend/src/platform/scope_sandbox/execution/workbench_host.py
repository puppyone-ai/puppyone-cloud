"""Lifecycle and file channels borrow one persisted provider resource.

They share framing with Pi but never launch a Loop. Disconnecting an API request
only closes its process/channel; it never pauses or deletes the workspace.
"""

from contextlib import suppress
from uuid import UUID
import asyncio

from .pi_worker import PiWorker


class WorkbenchChannel(PiWorker):
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
        self.attached = True

    def control_command(self):
        return [
            "node",
            f"/opt/puppyone-agent/workbench-{self.purpose}.mjs",
            self.workbench_id,
            self.generation,
        ]

    def attached_control_command(self):
        return self.control_command()

    async def connect(self, connection=None):
        if connection is not None:
            self.sandbox = connection
        else:
            self.sandbox = await self.runtime.running_connection(self)
        await self._open_control()
        return self

    async def close(self):
        self.reconnecting = True
        try:
            await self._disconnect_output()
            if self.process:
                self.process.stdin.close()
                try:
                    async with asyncio.timeout(5):
                        await self.process.wait()
                except TimeoutError:
                    self.process.terminate()
                    await self.process.wait()
            elif self.handle:
                # Kill just this root control command, not the compute resource.
                await self.handle.kill()
        finally:
            for task in (*self.git_tasks, self.reader, self.stderr_reader):
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
