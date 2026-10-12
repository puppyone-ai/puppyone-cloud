"""An admitted task connection, never the owner of a shared provider resource.

Only the durable workbench lifecycle may allocate, pause, replace or retire a
computer. This adapter cannot snapshot/restore its files on behalf of a Run.
"""

import asyncio
from contextlib import suppress
from uuid import UUID

from src.platform.scope_sandbox.execution.pi_worker import PiWorker


class WorkbenchTask(PiWorker):
    def __init__(self, execution_id, project_id, *, workbench_id, generation, task_uid, **options):
        super().__init__(execution_id, project_id, **options)
        self.workbench_id = str(UUID(workbench_id))
        self.workbench_generation = str(UUID(generation))
        self.task_execution_id = str(UUID(execution_id))
        if type(task_uid) is not int or not 20000 <= task_uid <= 59999:
            raise ValueError("Invalid admitted task UID")
        self.task_uid = task_uid

    def control_command(self):
        return [
            "node",
            "/opt/puppyone-agent/workbench-controller.mjs",
            str(self.task_uid),
            self.workbench_id,
            self.workbench_generation,
            self.task_execution_id,
        ]

    async def connect(self, resource):
        if (
            resource.get("workbench_id") != self.workbench_id
            or resource.get("workbench_generation") != self.workbench_generation
        ):
            raise PermissionError("Shared workbench resource binding mismatch")
        # Resource connection is shared; execution identity remains this Run's.
        await super().resume(resource)
        self.execution_id = self.task_execution_id
        self.restore_point = None

    async def create(self):
        raise PermissionError("Resource allocation belongs to the workbench lifecycle")

    async def start(self, config):
        if self.restore_point or config.get("resume_workspace"):
            raise PermissionError("A task cannot restore the workbench filesystem")
        await self._open_control()
        capability = await self.control("capabilities")
        if capability.get("shared_workbench") != 1:
            raise RuntimeError("Sandbox artifact lacks shared workbench task protocol")
        await self.control("prepare", {**config["workspace"], "project_id": self.project_id})
        await self.send({"type": "start", "config": config})

    async def snapshot(self):
        raise PermissionError("Resource snapshots belong to the workbench lifecycle")

    async def pause(self):
        raise PermissionError("Resource pause belongs to the workbench lifecycle")

    async def stop(self):
        """Quiesce just this UID and close its channel; never kill/pause the VM."""
        self.reconnecting = True
        try:
            if self.reader and not self.reader.done():
                async with asyncio.timeout(20):
                    await self.control("park")
            if self.process:
                self.process.stdin.close()
                async with asyncio.timeout(15):
                    await self.process.wait()
        finally:
            await self._disconnect_output()
            for pending in (*self.git_tasks, self.reader, self.stderr_reader):
                if pending:
                    pending.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await pending

    @staticmethod
    async def cleanup(resource, *, store=None):
        raise PermissionError("Resource deletion belongs to the workbench lifecycle")

    async def retain(self):
        # Shared resources are already durably owned; a Run cannot transfer them.
        return None
