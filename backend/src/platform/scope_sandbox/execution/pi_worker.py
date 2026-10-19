"""Pi-specific process transport using the existing sandbox provider registry.

No host mounts, model keys or shell interpolation of project data. Providers
expose the same framed channel. This module does not own Agent run state.
"""

import asyncio
import json
import time
from contextlib import suppress
from uuid import uuid4

from src.config import settings
from src.infra.supabase.instrumentation import database_stage
from src.platform.scope_sandbox.execution.store import ExecutionSession, durable_execution_store
from src.platform.scope_sandbox.execution.worker_port import WorkerDisconnected, WorkerLost

from .providers.port import SEND_TIMEOUT
from .providers.registry import runtime_provider

MAX_FRAME = 192 * 1024 * 1024


class PiWorker:
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
        self.store = store if store is not None else durable_execution_store()
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

    async def _docker(self, *args):
        proc = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode:
            raise RuntimeError(
                "Sandbox provider command failed: " + stderr.decode(errors="replace")[:300]
            )
        return stdout.decode().strip()

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

    async def _output(self, chunk):
        self.buffer += chunk
        if len(self.buffer.encode()) > MAX_FRAME:
            raise RuntimeError("Pi control frame too large")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
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
            with suppress(Exception):
                await self.send({"type": "reply", "id": frame["id"], "error": type(exc).__name__})

    async def _disconnected(self):
        if self.reconnecting:
            return
        for future in self.controls.values():
            if not future.done():
                future.set_exception(WorkerDisconnected("Sandbox control connection closed"))
        await self.queue.put({"type": "disconnected"})

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

    async def send(self, value):
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        if len(data.encode()) > MAX_FRAME:
            raise ValueError("Pi input frame too large")
        try:
            async with asyncio.timeout(SEND_TIMEOUT), self.send_lock:
                await self.runtime.send(self, data, timeout=SEND_TIMEOUT)
        except TimeoutError as exc:
            raise WorkerDisconnected("Sandbox input delivery timed out") from exc

    async def receive(self):
        return await self.queue.get()

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
        await asyncio.to_thread(self.store.delete, self.execution_id)

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
