"""Pi-specific process transport using the existing sandbox provider registry.

No host mounts, model keys or shell interpolation of project data. Providers
expose the same framed channel. This module does not own Agent run state.
"""

import asyncio
import time
from contextlib import suppress

from src.infra.execution.runner import run_blocking
from src.infra.supabase.instrumentation import database_stage
from src.platform.sandbox import runtime_provider
from src.platform.workspace.execution.store import ExecutionSession, durable_execution_store
from src.platform.workspace.execution.worker_port import WorkerLost

from .control_channel import ControlChannel


class PiWorker(ControlChannel):
    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store if store is not None else durable_execution_store()

    @classmethod
    async def attach(cls, resource, project_id):
        worker = cls(
            resource["session_id"],
            project_id,
            provider=resource["provider"],
            image=resource.get("image_id"),
            artifact=resource.get("template"),
        )
        worker.resource = dict(resource)
        worker.attached = True
        await worker.runtime.attach(worker)
        await worker._open_control()
        return worker

    async def create(self):
        if self.restore_point:
            if (
                self.restore_point["project_id"] != self.project_id
                or self.restore_point["provider"] != self.provider
            ):
                raise PermissionError("Recovery workspace binding mismatch")
            self.runtime.bind_recovery(self)
        await self.runtime.allocate(self)
        self.resource["allocation_pending"] = False
        now = time.time()
        if not self.resource.get("workspace_id") and not self.resource.get("workbench_id"):
            self.store.put(
                ExecutionSession(
                    self.execution_id,
                    self.provider,
                    self.resource["resource_id"],
                    False,
                    now,
                    now,
                    project_id=self.project_id,
                )
            )
        return dict(self.resource)

    async def resume(self, resource):
        fresh_resource, fresh_execution = dict(self.resource), self.execution_id
        try:
            return await self._resume_resource(resource)
        except WorkerLost:
            # A cold replacement must have a NEW provider identity. A delayed
            # cleanup of the missing generation must never kill its replacement.
            self.resource, self.execution_id = fresh_resource, fresh_execution
            self.sandbox, self.attached, self.reused = None, False, False
            raise

    async def _resume_resource(self, resource):
        if (
            resource.get("project_id") != self.project_id
            or resource.get("provider") != self.provider
        ):
            raise PermissionError("Workspace resource binding mismatch")
        self.resource = dict(resource)
        self.execution_id = resource["session_id"]
        self.restore_point = None
        await self.runtime.resume(self)
        self.reused = True
        return dict(self.resource)

    async def pause(self):
        # End the run's processes/relay, then park the SAME compute resource.
        # Docker PID 1 is a persistent supervisor, not this control connection.
        self.reconnecting = True
        try:
            if not self.parked:
                await self.control("park")
                self.parked = True
            await self.runtime.pause(self)
            for task in (self.reader, self.stderr_reader):
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
        finally:
            self.reconnecting = False

    def control_command(self):
        return ["node", "/opt/puppyone-agent/controller.mjs"]

    def attached_control_command(self):
        # Only the Docker launcher activates providers/docker-recovery.mjs, so the
        # flag belongs to this path rather than to the shared control command.
        return [*self.control_command(), "--docker-recovery"]

    async def _open_control(self):
        await self.runtime.open_channel(self)

    async def prepare(self, config):
        await self._open_control()
        capabilities = await self.control("capabilities")
        if capabilities.get("workspace_lifecycle") != 1:
            raise RuntimeError("Sandbox artifact does not support Session workspace lifecycle")
        expected_recovery = self.runtime.capabilities.recovery_backend
        if (
            capabilities.get("recovery_version") != 1
            or capabilities.get("recovery_backend") != expected_recovery
        ):
            raise RuntimeError("Sandbox recovery backend mismatch; rebuild worker artifact")
        workspace = config["workspace"]
        await self.control(
            "prepare",
            {
                **workspace,
                "project_id": self.project_id,
                "restore": self.runtime.restore_directory(self.restore_point),
                "resume": bool(config.get("resume_workspace")),
                "reuse": self.reused,
            },
        )
        if config.get("resume_workspace") and self.restore_point:
            self.recovery = self.restore_point
        else:
            self.recovery = await self.snapshot()
        return self.recovery

    async def start(self, config):
        # Compatibility entry point for existing direct worker callers.
        await self.prepare(config)
        await self.send({"type": "start", "config": config})

    async def touch(self):
        if self.resource.get("workspace_id"):
            await self.runtime.renew(self)
            return
        session = self.store.get(self.execution_id)
        if session:
            session.last_activity = time.time()
            self.store.put(session)

    async def stop(self):
        tasks = tuple(self.git_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.reconnecting = True
        await self._disconnect_output()
        await self.cleanup(self.resource, store=self.store)
        for task in (self.reader, self.stderr_reader):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        if self.process:
            await self.process.wait()

    async def snapshot(self):
        """Provider-owned recovery. No file bytes or Git objects cross this API."""
        with database_stage("provider_snapshot"):
            return await self._snapshot()

    async def _snapshot(self):
        result = await self.runtime.snapshot(self)
        self.recovery = result
        return dict(result)

    async def retain(self):
        """Transfer resource deletion from the idle reaper to the durable run owner."""
        await run_blocking(self.store.delete, self.execution_id)

    @staticmethod
    async def resolve_resources(resource):
        provider = runtime_provider(resource["provider"])
        try:
            return await provider.resolve(resource)
        finally:
            await provider.aclose()

    @staticmethod
    async def cleanup(resource, *, store=None):
        store = store if store is not None else durable_execution_store()
        provider = runtime_provider(resource["provider"])
        try:
            await provider.cleanup(resource)
        finally:
            await provider.aclose()
        store.delete(resource["session_id"])
