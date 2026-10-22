"""Provider-independent, durable workspace lifecycle and readiness.

HTTP records intent; background owners execute it under the existing SQL fence.
Connection health is checked independently of durable resource state.
"""

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime

from src.config import settings
from src.exceptions import NotFoundException
from src.platform.authorization.models import ProjectAction

from .errors import access_failure
from .execution.runtime_contract import WorkspaceUnavailable, validate_runtime
from .execution.worker_port import WorkerDisconnected, WorkerLost

logger = logging.getLogger(__name__)


def meter_id(row):
    return f"cloud-workbench:{row['id']}:{row['generation']}:{row['compute_epoch']}"


def failure(exc):
    if isinstance(exc, WorkspaceUnavailable):
        return exc.code, exc.retryable
    if getattr(exc, "status_code", None) == 402:
        return "workbench_plan_required", False
    if (
        isinstance(exc, (PermissionError, NotFoundException))
        or getattr(exc, "status_code", None) is not None
    ):
        return "workbench_access_denied", False
    if isinstance(exc, TimeoutError):
        return "workbench_start_timeout", True
    if isinstance(exc, WorkerLost):
        return "workbench_requires_recovery", False
    return "workbench_connection_unavailable", True


class WorkbenchLifecycle:
    def __init__(self, service):
        self.service = service
        self.repository = service.repository
        self.transport, self.filesystem, self.billing = (
            service.transport,
            service.filesystem,
            service.billing,
        )
        self.channel_factory, self.providers = service.channel_factory, service.providers
        self.channels, self.check = service.channels, service.check
        self.checked_until = 0
        self.preflight_lock = asyncio.Lock()
        self.active = {}

    def context(self, *args):
        return self.service.context(*args)

    async def preflight(self, row=None):
        provider = self.providers(None)
        binding = (
            (row or {}).get("binding") if (row or {}).get("state") != "retired" else None
        ) or {
            "provider": provider.name,
            "artifact": provider.artifact(settings.CLOUD_AGENT_IMAGE),
        }
        if binding["provider"] != provider.name:
            raise WorkspaceUnavailable("workbench_provider_not_configured")
        try:
            if not provider.capabilities.shared_workspace or not provider.capabilities.native_files:
                raise ValueError("Workspace capabilities required")
            provider.validate(binding["artifact"], (row or {}).get("resource"))
            async with self.preflight_lock:
                if time.monotonic() >= self.checked_until:
                    async with asyncio.timeout(20):
                        await provider.preflight(binding["artifact"])
                    self.checked_until = time.monotonic() + 30
        except ValueError as exc:
            raise WorkspaceUnavailable("workbench_provider_not_configured") from exc
        except WorkspaceUnavailable:
            raise
        except Exception as exc:
            raise WorkspaceUnavailable("workbench_connection_unavailable", retryable=True) from exc
        finally:
            await provider.aclose()
        return provider

    async def ready(self, row):
        async with self.channels.borrow(row) as host:
            await self.filesystem.operation(host, {"action": "list", "path": ""})

    async def request(self, user, project, row=None):
        context, _ = await asyncio.to_thread(self.context, user, project, ProjectAction.AGENT_RUN)
        row = (
            row
            or context["workbench"]
            or await asyncio.to_thread(self.repository.default, user, project, context["revision"])
        )
        if row["user_id"] != user or row["project_id"] != project:
            raise PermissionError("Workbench ownership mismatch")
        provider = await self.preflight(row)
        if row["state"] in {"retained", "cleanup_pending"}:
            raise WorkspaceUnavailable("workbench_requires_recovery")
        if row["state"] == "running":
            try:
                row = await asyncio.to_thread(self.repository.activity, row, context["revision"])
                await self.check(row)
                async with asyncio.timeout(35):
                    await self.ready(row)
            except (WorkerDisconnected, TimeoutError) as exc:
                code, retryable = failure(exc)
                raise WorkspaceUnavailable(code, retryable=retryable) from exc
            return row
        binding = (row["binding"] if row["state"] != "retired" else None) or {
            "provider": provider.name,
            "artifact": provider.artifact(settings.CLOUD_AGENT_IMAGE),
        }
        return await asyncio.to_thread(
            self.repository.request_open, row, binding, context["revision"]
        )

    async def ensure_running(self, run, row, *, base=None):
        row = await self.request(run["user_id"], run["project_id"], row)
        # Agent and internal callers wait; only the lifecycle worker performs I/O.
        async with asyncio.timeout(620):
            while row["state"] != "running" or row.get("open_requested"):
                if row.get("operation_error"):
                    raise WorkspaceUnavailable(
                        row["operation_error"], retryable=row.get("operation_retryable", False)
                    )
                await asyncio.sleep(0.25)
                row = await asyncio.to_thread(self.repository.get, row["id"])
            await self.check(row)
            await self.ready(row)
            return row

    async def progress(self, row, phase):
        await asyncio.to_thread(self.repository.progress, row, phase)

    async def execute(self, original):
        deadline = datetime.fromisoformat(original["open_deadline"])
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            await asyncio.to_thread(self.repository.expire_open, original)
            return
        if original["state"] == "pausing":
            return  # The same lifecycle's maintenance finishes pause before wake.
        claimed = await asyncio.to_thread(self.repository.acquire, original, original["binding"])
        if not claimed["acquired"]:
            return
        row = claimed["workbench"]
        phase = "allocating" if row["state"] == "allocating" else "connecting"
        await self.progress(row, phase)

        async def heartbeat():
            while True:
                await asyncio.sleep(30)
                current = await asyncio.to_thread(self.repository.get, row["id"])
                if current["operation_id"] != row["operation_id"] or not current["open_requested"]:
                    raise WorkspaceUnavailable("workbench_operation_replaced")
                await self.progress(current, current["operation_phase"])

        async def start():
            async with asyncio.timeout(remaining):
                await self.preflight(row)
                _, grant = await asyncio.to_thread(
                    self.context, row["user_id"], row["project_id"], ProjectAction.AGENT_RUN
                )
                return await self._start(row, grant)

        operation, heartbeat_task = asyncio.create_task(start()), asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait(
                [operation, heartbeat_task], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        except asyncio.CancelledError:
            # A successor can resolve the persisted allocation after lease expiry.
            raise
        except Exception as exc:
            code, retryable = failure(exc)
            self.checked_until = 0
            current = await asyncio.to_thread(self.repository.get, row["id"])
            if current and current["operation_id"] == row["operation_id"]:
                with suppress(Exception):
                    await asyncio.to_thread(
                        self.repository.progress, current, "failed", code, retryable
                    )
            logger.exception(
                "workbench_start_failed", extra={"workbench_id": row["id"], "code": code}
            )
        finally:
            operation.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(operation, heartbeat_task, return_exceptions=True)
            await self.billing.detach_session(meter_id(row))

    async def serve(self):
        async def run(row):
            try:
                await self.execute(row)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.checked_until = 0
                logger.exception("workbench_dispatch_failed", extra={"workbench_id": row["id"]})

        try:
            while True:
                try:
                    # Failed credentials are recorded on the claimed operation;
                    # they must not strand every pending request in the queue.
                    provider = self.providers(None)
                    for row in await asyncio.to_thread(self.repository.pending, provider.name):
                        if row["id"] not in self.active and len(self.active) < 4:
                            task = asyncio.create_task(run(row))
                            self.active[row["id"]] = task
                            task.add_done_callback(
                                lambda _, identity=row["id"]: self.active.pop(identity, None)
                            )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.checked_until = 0
                    logger.exception("workbench_provider_preflight_failed")
                    await asyncio.sleep(5)
                await asyncio.sleep(0.5)
        finally:
            tasks = list(self.active.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

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
        await self.progress(row, "connecting")
        capability = await host.control("capabilities")
        validate_runtime(capability.get("runtime"))
        if capability.get("workbench_lifecycle") != 1:
            raise RuntimeError("Workspace artifact does not implement lifecycle initialization")
        if row["state"] == "allocating":
            await self.progress(row, "initializing")
            await host.control("initialize", host.resource["initialization"])
        else:
            await host.control("ready")
        await self.progress(row, "checking_files")
        # Ready includes the actual native file service, not merely a running VM.
        ready_row = {**row, "resource": host.resource}
        await self.ready(ready_row)
        # Recheck authority before accepting work after a potentially long clone.
        await asyncio.to_thread(
            self.context, row["user_id"], row["project_id"], ProjectAction.AGENT_RUN
        )
        return await asyncio.to_thread(self.repository.ready, row, host.resource)

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

    async def pause(self, row):
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
        paused = await asyncio.to_thread(self.repository.transition, row, "paused", host.resource)
        await asyncio.to_thread(self.repository.progress, paused, "paused")

    async def reap(self):
        provider = await self.preflight()
        for row in await asyncio.to_thread(self.repository.due, provider.name):
            if row.get("open_requested") and row["state"] != "pausing":
                continue
            owned = None
            try:
                if row["state"] == "running":
                    claimed = await asyncio.to_thread(self.repository.idle, row)
                    if claimed["acquired"]:
                        owned = claimed["workbench"]
                        await self.pause(owned)
                        continue
                    try:
                        await self.check(row)
                        await self.billing.resume_session(
                            meter_id(row), project_id=row["project_id"], actor_id=row["user_id"]
                        )
                        await self.billing.detach_session(meter_id(row))
                        host = self.channel_factory(row)
                        connection = await self.service.connection(row)
                        try:
                            await host.runtime.connect_task(host, host.resource, connection)
                            await host.runtime.renew(host)
                        finally:
                            await host.runtime.aclose()
                    except Exception as exc:
                        if not access_failure(exc):
                            raise
                        claimed = await asyncio.to_thread(
                            self.repository.lifecycle_claim, row, suspend=True
                        )
                        if claimed["acquired"]:
                            owned = claimed["workbench"]
                            await self.pause(owned)
                        else:
                            raise
                elif row["state"] in {"pausing", "allocating", "resuming", "retained"}:
                    # Pausing/starting may belong to another API/worker. Only an
                    # expired fenced claim may be taken over after a crash.
                    claimed = await asyncio.to_thread(self.repository.lifecycle_claim, row)
                    if claimed["acquired"]:
                        current = owned = claimed["workbench"]
                        if current["state"] == "pausing":
                            await self.pause(current)
                        else:
                            await self._preserve_failed_start(current)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                current = await asyncio.to_thread(self.repository.get, row["id"])
                if owned and current and current.get("operation_id") == owned["operation_id"]:
                    with suppress(Exception):
                        code, retryable = failure(exc)
                        await asyncio.to_thread(
                            self.repository.progress, current, "failed", code, retryable
                        )
                logger.exception("workbench_maintenance_retry", extra={"workbench_id": row["id"]})
