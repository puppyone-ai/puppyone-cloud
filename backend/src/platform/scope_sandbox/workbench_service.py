"""Private workspace lifecycle, independent of conversations and provider SDKs."""

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import HTTPException

from src.config import settings
from src.platform.access.adapters.agent.runtime.filesystem.port import FilesystemError
from src.platform.authorization.factory import build_authorization_service
from src.platform.authorization.models import ProjectAction
from src.platform.scope_sandbox.execution.providers.registry import runtime_provider
from src.platform.scope_sandbox.execution.workbench_host import WorkbenchChannel, WorkbenchChannels
from src.platform.scope_sandbox.execution.worker_port import WorkerDisconnected

from .workbench_repository import WorkbenchRepository

logger = logging.getLogger(__name__)


def public_workbench(row):
    if row is None:
        return None
    return {key: row[key] for key in ("id", "generation", "project_id", "state", "target_ref")}


def meter_id(row):
    return f"cloud-workbench:{row['id']}:{row['generation']}:{row['compute_epoch']}"


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

    async def current(self, user, project):
        context, _ = await asyncio.to_thread(
            self.context, user, project, ProjectAction.CONTENT_READ
        )
        return context["workbench"]

    async def open(self, user, project):
        context, _ = await asyncio.to_thread(self.context, user, project, ProjectAction.AGENT_RUN)
        row = context["workbench"] or await asyncio.to_thread(
            self.repository.default, user, project, context["revision"]
        )
        return await self.ensure_running({"user_id": user, "project_id": project}, row)

    async def ensure_running(self, run, workbench, *, base=None):
        user, project = run["user_id"], run["project_id"]
        if workbench["user_id"] != user or workbench["project_id"] != project:
            raise PermissionError("Workbench ownership mismatch")
        async with asyncio.timeout(170):
            while True:
                _, grant = await asyncio.to_thread(
                    self.context, user, project, ProjectAction.AGENT_RUN
                )
                row = await asyncio.to_thread(self.repository.get, workbench["id"])
                if row["owner_deleted_at"] is not None:
                    raise PermissionError("Workbench owner is unavailable")
                if row["state"] == "running":
                    await self.check(row)
                    return row
                if row["state"] in {"retained", "cleanup_pending"}:
                    raise HTTPException(409, {"code": "workbench_requires_recovery"})
                provider = self.providers(row["binding"].get("provider"))
                binding = row["binding"] or {
                    "provider": provider.name,
                    "artifact": provider.artifact(settings.CLOUD_AGENT_IMAGE),
                }
                if (
                    not binding.get("artifact")
                    or not provider.capabilities.shared_workspace
                    or not provider.capabilities.native_files
                ):
                    raise HTTPException(503, {"code": "workbench_provider_not_configured"})
                try:
                    provider.validate(binding["artifact"], row["resource"])
                except ValueError as exc:
                    raise HTTPException(503, {"code": "workbench_provider_not_configured"}) from exc
                claimed = await asyncio.to_thread(self.repository.acquire, row, binding)
                row = claimed["workbench"]
                if not claimed["acquired"]:
                    await asyncio.sleep(0.25)
                    continue
                try:
                    return await self._start(row, grant)
                except BaseException:
                    # Reconciliation owns any uncertain allocation outcome. Never
                    # replace a failed/late allocation with an empty resource.
                    logger.exception(
                        "workbench_start_unconfirmed", extra={"workbench_id": row["id"]}
                    )
                    raise
                finally:
                    await self.billing.detach_session(meter_id(row))

    async def _start(self, row, grant):
        host = self.channel_factory(row)
        try:
            return await self._start_host(row, grant, host)
        finally:
            await host.close()

    async def _start_host(self, row, grant, host):
        # One meter per compute epoch, shared by every Chat and file channel.
        await self.billing.ensure_session(
            audit_context={
                "source": "sandbox",
                "run_id": meter_id(row),
                "project_id": row["project_id"],
                "user_id": row["user_id"],
                "org_id": grant.org_id,
            }
        )
        await self.billing.check_session(meter_id(row))
        if row["state"] == "allocating":
            fresh = row["resource"] is None
            if fresh:
                target = await asyncio.to_thread(
                    self.transport.describe, row["project_id"], grant, target_ref=row["target_ref"]
                )
                source = (
                    target
                    if target["expected_oid"]
                    else await asyncio.to_thread(self.transport.describe, row["project_id"], grant)
                )
                host.resource["initialization"] = {
                    "project_id": row["project_id"],
                    "object_format": source["object_format"],
                    "target_ref": row["target_ref"],
                    "source_ref": source["target_ref"],
                    "base_oid": source["expected_oid"],
                    "readonly": False,
                }
                host.resource["git_base"] = source
                # Allocation intent, seed and exact generation survive response loss.
                row = await asyncio.to_thread(
                    self.repository.transition, row, "allocating", host.resource
                )
                await host.create()
                row = await asyncio.to_thread(
                    self.repository.transition, row, "allocating", host.resource
                )
            else:
                matches = await host.runtime.resolve(host.resource)
                if len(matches) != 1:
                    raise WorkerDisconnected(
                        "Allocation outcome is ambiguous; preserve the original intent"
                    )
                host.resource = matches[0]
                # Recovered intents carry the original pinned artifact.
                await host.runtime.resume(host)
        elif row["state"] == "resuming":
            await host.runtime.resume(host)
        else:
            raise WorkerDisconnected("Workspace lifecycle transition is not startable")

        async def exchange(frame):
            if "git-receive-pack" in frame.get("path", ""):
                raise PermissionError("Workspace initialization has no publication capability")
            _, current = await asyncio.to_thread(
                self.context, row["user_id"], row["project_id"], ProjectAction.CONTENT_READ
            )
            return await self.transport.exchange(
                row["project_id"], current, frame, base=host.resource["git_base"]
            )

        host.git_handler = exchange
        await host._open_control()
        capability = await host.control("capabilities")
        if capability.get("workbench_lifecycle") != 1:
            raise RuntimeError("Workspace artifact does not implement lifecycle initialization")
        if row["state"] == "allocating":
            await host.control("initialize", host.resource["initialization"])
        else:
            await host.control("ready")
        # Recheck authority before accepting work after a potentially long clone.
        await asyncio.to_thread(
            self.context, row["user_id"], row["project_id"], ProjectAction.AGENT_RUN
        )
        return await asyncio.to_thread(self.repository.transition, row, "running", host.resource)

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
        row = await asyncio.to_thread(
            self.repository.activity, row, context["revision"], write=write
        )
        await self.billing.check_session(meter_id(row))
        if watch:
            # A subscriber must never monopolize a pooled request channel.
            host = self.channel_factory(row, purpose="files")
            try:
                await host.connect()
                yield host
            finally:
                await host.close()
        else:
            async with self.channels.borrow(row) as host:
                yield host

    async def close(self):
        await self.channels.close()

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
        async with self.files(user, project, generation, watch=True) as channel:
            await self.filesystem.watch(channel)
            yield {"generation": generation, "reset": True}
            while True:
                await self.bound(user, project, generation)
                try:
                    frame = await asyncio.wait_for(channel.receive(), timeout=10)
                except TimeoutError:
                    yield None
                    continue
                if frame.get("type") != "workspace_changed":
                    raise WorkerDisconnected("Workspace watch disconnected")
                yield {"generation": generation, "reset": True}

    async def _preserve_failed_start(self, row):
        host = self.channel_factory(row)
        try:
            if row["resource"] is None:
                # Intent is persisted before allocation, so no provider resource exists.
                if settings.RUNTIME_METERING_MODE != "disabled":
                    await self.billing.cancel_session(meter_id(row))
            else:
                matches = await host.runtime.resolve(host.resource)
                if len(matches) != 1:
                    # Keep reconciling a possibly late allocation. Absence after a
                    # timeout is not permission to create a second empty workspace.
                    raise WorkerDisconnected("Allocation outcome requires reconciliation")
                host.resource = matches[0]
                result = await host.runtime.pause_resource(host)
                host.resource["preservation"] = result.receipt(host.resource)
                if (
                    settings.RUNTIME_METERING_MODE != "disabled"
                    and not await self.billing.finish_session(meter_id(row), retry=True)
                ):
                    raise WorkerDisconnected("Workspace settlement awaits confirmation")
            if row["state"] != "retained":
                await asyncio.to_thread(
                    self.repository.transition,
                    row,
                    "retained",
                    host.resource if row["resource"] is not None else None,
                )
        finally:
            await host.runtime.aclose()

    async def _pause(self, row):
        await self.channels.invalidate(row["id"])
        host = self.channel_factory(row)
        try:
            result = await host.runtime.pause_resource(host)
            host.resource["preservation"] = result.receipt(host.resource)
        finally:
            await host.runtime.aclose()
        # Settlement precedes paused ACK and the next compute epoch.
        if settings.RUNTIME_METERING_MODE != "disabled" and not await self.billing.finish_session(
            meter_id(row), retry=True
        ):
            raise WorkerDisconnected("Workspace settlement awaits confirmation")
        await asyncio.to_thread(self.repository.transition, row, "paused", host.resource)

    async def reap(self):
        for row in await asyncio.to_thread(self.repository.due):
            try:
                if row["state"] == "running":
                    claimed = await asyncio.to_thread(self.repository.idle, row)
                    if claimed["acquired"]:
                        await self._pause(claimed["workbench"])
                        continue
                    try:
                        await self.check(row)
                        await self.billing.resume_session(
                            meter_id(row), project_id=row["project_id"], actor_id=row["user_id"]
                        )
                        await self.billing.detach_session(meter_id(row))
                        host = self.channel_factory(row)
                        connection = await self.connection(row)
                        try:
                            await host.runtime.connect_task(host, host.resource, connection)
                            await host.runtime.renew(host)
                        finally:
                            await host.runtime.aclose()
                    except Exception:
                        claimed = await asyncio.to_thread(
                            self.repository.lifecycle_claim, row, suspend=True
                        )
                        if claimed["acquired"]:
                            await self._pause(claimed["workbench"])
                        else:
                            raise
                elif row["state"] in {"pausing", "allocating", "resuming", "retained"}:
                    # Pausing/starting may belong to another API/worker. Only an
                    # expired fenced claim may be taken over after a crash.
                    claimed = await asyncio.to_thread(self.repository.lifecycle_claim, row)
                    if claimed["acquired"]:
                        current = claimed["workbench"]
                        if current["state"] == "pausing":
                            await self._pause(current)
                        else:
                            await self._preserve_failed_start(current)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("workbench_maintenance_retry", extra={"workbench_id": row["id"]})


def build_workbench_service(*, engine=None):
    from src.platform.access.adapters.agent.runtime.filesystem.sandbox import SandboxFilesystem
    from src.platform.billing.runtime import get_runtime_metering_service
    from src.version_engine.adapters.git.run_transport import RunGitTransport
    from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container

    engine = engine or build_worker_version_engine_container()
    return WorkbenchService(
        RunGitTransport(engine.repo_manager), SandboxFilesystem(), get_runtime_metering_service()
    )
