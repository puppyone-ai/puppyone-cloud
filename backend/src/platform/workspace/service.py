"""Private workspace access, authorization, audit and live-file use cases."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.authorization.factory import build_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.sandbox import runtime_provider, workspace_stage
from src.platform.workspace.execution.workbench_host import WorkbenchChannel, WorkbenchChannels
from src.platform.workspace.execution.worker_port import WorkerDisconnected, WorkerLost
from src.platform.workspace.filesystem.port import FilesystemError, FilesystemPort, GitTransport

from .admission import AdmissionSnapshots
from .errors import WorkspaceError
from .lifecycle import WorkbenchLifecycle, meter_id
from .opening import WorkspaceOpening, same_runtime
from .repository import WorkbenchRepository


@asynccontextmanager
async def _borrowed(channels, row):
    try:
        async with channels.borrow(row) as host:
            yield host
    except WorkerLost as exc:
        # Compute is gone and the disk is the workspace: maintenance pauses it,
        # and reopening recreates compute on the same disk.
        raise WorkspaceError(503, {"code": "workbench_preparing", "retryable": True}) from exc


class WorkbenchAdmission:
    """Synchronous request admission creates identity only, never compute."""

    def __init__(self, repository=None, authorization=None):
        self.repository = repository or WorkbenchRepository()
        self.authorization = authorization or build_authorization_service()
        self.snapshots = AdmissionSnapshots()

    def context(self, user, project, action):
        try:
            context = self.repository.context(user, project)
            grant = self.authorization.authorize_facts(context["facts"], project, user, action)
        except Exception:
            self.snapshots.discard(user, project)
            raise
        self.snapshots.put(user, project, context)
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
            raise WorkspaceError(404, {"code": "workbench_not_found"})
        if not row["is_default"]:
            raise WorkspaceError(409, {"code": "interactive_workspace_required"})
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
        transport: GitTransport,
        filesystem: FilesystemPort,
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
        self.opening = WorkspaceOpening(self)

    async def current(self, user, project):
        context, _ = await run_blocking(
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
        context, _ = await run_blocking(
            self.context, row["user_id"], row["project_id"], ProjectAction.AGENT_RUN
        )
        # Context already contains the interactive default workspace. Automation
        # workspaces have separate identities and still need their exact row.
        current = (
            context["workbench"]
            if row.get("is_default")
            else await run_blocking(self.repository.get, row["id"])
        )
        if (
            not current
            or current["id"] != row["id"]
            or current["user_id"] != row["user_id"]
            or current["project_id"] != row["project_id"]
            or current["generation"] != row["generation"]
            or current["state"] != "running"
            or current["owner_deleted_at"] is not None
            or current["binding"] != row["binding"]
            or current.get("compute_epoch") != row.get("compute_epoch")
            or current.get("resource") != row.get("resource")
        ):
            raise WorkerDisconnected("Workspace ownership changed")
        await self.billing.check_session(meter_id(row))

    async def connection(self, row, *, capabilities=()):
        # Used by already-admitted tasks, including revoked-task cleanup. The
        # task ledger holds occupancy until stop ACK, so idle pause cannot race.
        current = await run_blocking(self.repository.get, row["id"])
        if (
            not current
            or current["generation"] != row["generation"]
            or current["state"] != "running"
        ):
            raise WorkerDisconnected("Workbench is not running")
        if capabilities:
            # Optional execution protocols must be checked before reserving a
            # process identity. Old artifacts cannot acknowledge their cleanup.
            async with asyncio.timeout(35), self.channels.borrow(current) as host:
                available = host.service_receipt["runtime"]["capabilities"]
                if not set(capabilities).issubset(available):
                    raise WorkspaceError(409, {"code": "workspace_capability_missing"})
                return host.connection
        host = self.channel_factory(row)
        try:
            return await host.runtime.running_connection(host)
        finally:
            await host.runtime.aclose()

    async def bound(self, user, project, generation, *, write=False):
        action = ProjectAction.CONTENT_WRITE if write else ProjectAction.CONTENT_READ
        context, _ = await run_blocking(self.context, user, project, action)
        row = context["workbench"]
        if row is None:
            raise WorkspaceError(404, {"code": "workbench_not_found"})
        if row["generation"] != generation:
            raise WorkspaceError(409, {"code": "workbench_generation_changed"})
        if row["state"] != "running":
            raise WorkspaceError(409, {"code": "workbench_not_running", "state": row["state"]})
        return row, context

    @asynccontextmanager
    async def files(self, user, project, generation, *, write=False, watch=False):
        cached = self.snapshots.get(user, project) if not watch else None
        row = (cached or {}).get("workbench")
        if row and row["state"] == "running" and row["generation"] == generation:
            action = ProjectAction.CONTENT_WRITE if write else ProjectAction.CONTENT_READ
            try:
                self.authorization.authorize_facts(cached["facts"], project, user, action)
            except Exception:
                cached = None
            if cached:
                try:
                    with workspace_stage("files.admission_activity"):
                        row = await run_blocking(
                            self.repository.activity, row, cached["revision"], write=write
                        )
                except Exception as exc:
                    self.snapshots.discard(user, project)
                    if getattr(exc, "code", None) != "P0001" or "agent_context_changed" not in str(
                        exc
                    ):
                        raise
                    # Revision changed: load fresh facts, never use the old grant.
                else:
                    await self.billing.check_session(meter_id(row))
                    async with _borrowed(self.channels, row) as host:
                        yield host
                    return
        with workspace_stage("files.admission"):
            row, context = await self.bound(user, project, generation, write=write)
        if not watch:
            with workspace_stage("files.activity"):
                row = await run_blocking(
                    self.repository.activity, row, context["revision"], write=write
                )
        await self.billing.check_session(meter_id(row))
        async with _borrowed(self.channels, row) as host:
            yield host

    async def close(self):
        from src.platform.sandbox import close_provider_transports

        await self.opening.observers.close()
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
                await run_blocking(
                    self.repository.audit, user, project, generation, operation, audit_id, "intent"
                )
            try:
                with workspace_stage("files.operation"):
                    result = await self.filesystem.operation(channel, operation)
            except FilesystemError as exc:
                if write:
                    await run_blocking(
                        self.repository.audit,
                        user,
                        project,
                        generation,
                        operation,
                        audit_id,
                        "rejected",
                    )
                status = {"file_conflict": 409, "file_not_found": 404}.get(exc.code, 422)
                raise WorkspaceError(status, {"code": exc.code}) from exc
            if write:
                await run_blocking(
                    self.repository.audit,
                    user,
                    project,
                    generation,
                    operation,
                    audit_id,
                    "completed",
                )
            # A response is never handed to a newly-revoked or replaced reader.
            with workspace_stage("files.response_authorization"):
                current, _ = await self.bound(user, project, generation, write=write)
                if not same_runtime(channel.admitted_row, current):
                    raise WorkerDisconnected("Workspace changed during file operation")
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
