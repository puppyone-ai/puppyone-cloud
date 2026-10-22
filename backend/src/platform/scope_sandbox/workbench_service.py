"""Private workspace access, authorization, audit and live-file use cases."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from src.config import settings
from src.platform.access.adapters.agent.runtime.filesystem.port import FilesystemError
from src.platform.authorization.factory import build_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.scope_sandbox.execution.providers.registry import runtime_provider
from src.platform.scope_sandbox.execution.workbench_host import WorkbenchChannel, WorkbenchChannels
from src.platform.scope_sandbox.execution.worker_port import WorkerDisconnected

from .workbench_lifecycle import WorkbenchLifecycle, meter_id
from .workbench_repository import WorkbenchRepository


def public_workbench(row):
    if row is None:
        return None
    expired = bool(
        row.get("open_requested")
        and row.get("open_deadline")
        and datetime.fromisoformat(row["open_deadline"]) <= datetime.now(UTC)
    )
    return {
        **{key: row[key] for key in ("id", "generation", "project_id", "state", "target_ref")},
        "operation": {
            "pending": row.get("open_requested", False) and not expired,
            "phase": "failed" if expired else row.get("operation_phase"),
            "deadline": row.get("open_deadline"),
            "error": "workbench_start_timeout" if expired else row.get("operation_error"),
            "retryable": expired or row.get("operation_retryable", False),
        },
    }


class WorkbenchAdmission:
    """Synchronous request admission creates identity only, never compute."""

    def __init__(self, repository=None, authorization=None):
        self.repository = repository or WorkbenchRepository()
        self.authorization = authorization or build_authorization_service()

    def context(self, user, project, action):
        context = self.repository.context(user, project)
        grant = self.authorization.authorize_facts(context["facts"], project, user, action)
        return context, grant

    def admit(self, context, identity=None):
        if identity is None:
            row = self.repository.default(
                context.user_id, context.project_id, context.policy["revision"]
            )
        else:
            row = self.repository.get(identity)
        if (
            not row
            or row["user_id"] != context.user_id
            or row["project_id"] != context.project_id
            or row["owner_deleted_at"] is not None
        ):
            raise HTTPException(404, {"code": "workbench_not_found"})
        if not row["is_default"]:
            raise HTTPException(409, {"code": "interactive_workspace_required"})
        return row

    def automation(self, context, request):
        return self.repository.command(
            "automation",
            user=context.user_id,
            project=context.project_id,
            request=request,
            revision=context.policy["revision"],
        )


class WorkbenchService(WorkbenchAdmission):
    def __init__(
        self,
        transport,
        filesystem,
        billing,
        *,
        repository=None,
        authorization=None,
        channel_factory=WorkbenchChannel,
        providers=runtime_provider,
    ):
        super().__init__(repository, authorization)
        self.transport, self.filesystem, self.billing = transport, filesystem, billing
        self.channel_factory, self.providers = channel_factory, providers
        self.channels = WorkbenchChannels(
            channel_factory,
            idle_seconds=settings.CLOUD_AGENT_FILE_CHANNEL_IDLE_SECONDS,
            limit=settings.CLOUD_AGENT_FILE_CHANNEL_LIMIT,
        )
        self.lifecycle = WorkbenchLifecycle(self)

    async def current(self, user, project):
        context, _ = await asyncio.to_thread(
            self.context, user, project, ProjectAction.CONTENT_READ
        )
        return context["workbench"]

    async def request_open(self, user, project):
        return await self.lifecycle.request(user, project)

    async def open(self, user, project):
        return await self.ensure_running({"user_id": user, "project_id": project}, None)

    async def ensure_running(self, run, workbench, *, base=None):
        return await self.lifecycle.ensure_running(run, workbench, base=base)

    async def reap(self):
        await self.lifecycle.reap()

    async def check(self, row):
        current = await asyncio.to_thread(self.repository.get, row["id"])
        if (
            not current
            or current["generation"] != row["generation"]
            or current["state"] != "running"
            or current["owner_deleted_at"] is not None
            or current["binding"] != row["binding"]
        ):
            raise WorkerDisconnected("Workspace ownership changed")
        await asyncio.to_thread(
            self.context, row["user_id"], row["project_id"], ProjectAction.AGENT_RUN
        )
        await self.billing.check_session(meter_id(row))

    async def connection(self, row):
        # Used by already-admitted tasks, including revoked-task cleanup. The
        # task ledger holds occupancy until stop ACK, so idle pause cannot race.
        current = await asyncio.to_thread(self.repository.get, row["id"])
        if (
            not current
            or current["generation"] != row["generation"]
            or current["state"] != "running"
        ):
            raise WorkerDisconnected("Workbench is not running")
        host = self.channel_factory(row)
        try:
            return await host.runtime.running_connection(host)
        finally:
            await host.runtime.aclose()

    async def bound(self, user, project, generation, *, write=False):
        action = ProjectAction.CONTENT_WRITE if write else ProjectAction.CONTENT_READ
        context, _ = await asyncio.to_thread(self.context, user, project, action)
        row = context["workbench"]
        if row is None:
            raise HTTPException(404, {"code": "workbench_not_found"})
        if row["generation"] != generation:
            raise HTTPException(409, {"code": "workbench_generation_changed"})
        if row["state"] != "running":
            raise HTTPException(409, {"code": "workbench_not_running", "state": row["state"]})
        return row, context

    @asynccontextmanager
    async def files(self, user, project, generation, *, write=False, watch=False):
        row, context = await self.bound(user, project, generation, write=write)
        if not watch:
            row = await asyncio.to_thread(
                self.repository.activity, row, context["revision"], write=write
            )
        await self.billing.check_session(meter_id(row))
        async with self.channels.borrow(row) as host:
            yield host

    async def close(self):
        from .execution.providers.registry import close_provider_transports

        await self.channels.close()
        await close_provider_transports()

    async def file_operation(self, user, project, generation, operation):
        write = operation["action"] in {"write", "mkdir", "move", "remove"}
        # Bounded below the SQL minimum idle interval (60s); a file request
        # cannot outlive the activity admission that protects its connection.
        async with (
            asyncio.timeout(35),
            self.files(user, project, generation, write=write) as channel,
        ):
            audit_id = str(uuid4())
            if write:
                await asyncio.to_thread(
                    self.repository.audit, user, project, generation, operation, audit_id, "intent"
                )
            try:
                result = await self.filesystem.operation(channel, operation)
            except FilesystemError as exc:
                if write:
                    await asyncio.to_thread(
                        self.repository.audit,
                        user,
                        project,
                        generation,
                        operation,
                        audit_id,
                        "rejected",
                    )
                status = {"file_conflict": 409, "file_not_found": 404}.get(exc.code, 422)
                raise HTTPException(status, {"code": exc.code}) from exc
            if write:
                await asyncio.to_thread(
                    self.repository.audit,
                    user,
                    project,
                    generation,
                    operation,
                    audit_id,
                    "completed",
                )
            # A response is never handed to a newly-revoked or replaced reader.
            await self.bound(user, project, generation, write=write)
            return result

    async def events(self, user, project, generation):
        async with (
            self.files(user, project, generation, watch=True) as channel,
            self.filesystem.subscribe(channel) as events,
        ):
            await self.bound(user, project, generation)
            yield {"generation": generation, "reset": True}
            while True:
                await self.bound(user, project, generation)
                try:
                    frame = await asyncio.wait_for(events.get(), timeout=10)
                except TimeoutError:
                    yield None
                    continue
                if frame.get("type") != "workspace_changed":
                    raise WorkerDisconnected("Workspace watch disconnected")
                # Permission can change while waiting for a filesystem event.
                await self.bound(user, project, generation)
                # Preserve the public reset contract; path hints are additive.
                yield {"generation": generation, "reset": True, "paths": frame.get("paths")}


def build_workbench_service(*, engine=None):
    from src.platform.access.adapters.agent.runtime.control.git_transport import RunGitTransport
    from src.platform.access.adapters.agent.runtime.filesystem.sandbox import SandboxFilesystem
    from src.platform.billing.runtime import get_runtime_metering_service
    from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

    engine = engine or build_worker_version_engine_container()
    return WorkbenchService(
        RunGitTransport(engine.repo_manager), SandboxFilesystem(), get_runtime_metering_service()
    )
