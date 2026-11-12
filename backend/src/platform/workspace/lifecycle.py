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
from src.infra.dependency_diagnostics import dependency_failure
from src.infra.execution.runner import run_blocking
from src.shared_types.grants import ProjectAction
from src.platform.sandbox import (
    WorkspaceDiskLost,
    profile_artifact,
    profile_provider,
    settings_profile,
    workspace_providers,
    workspace_stage,
)

from .errors import access_failure, context_changed
from .execution.runtime_contract import WorkspaceUnavailable, validate_runtime
from .execution.worker_port import WorkerDisconnected, WorkerLost
from .opening import awaiting_files

logger = logging.getLogger(__name__)

# A save gets a few tries inside one operation lease, then the pause goes on without it.
SAVE_ATTEMPTS = 3
SAVE_RETRY_SECONDS = 5
SAVE_BUDGET_SECONDS = 150
# Past this a pause stops retrying: the machine goes back to running, or to retained.
PAUSE_LIMIT_SECONDS = 600


def meter_source(row):
    """Who a compute epoch works for, which decides whose hours it spends.

    An external Agent's machine and an automation's workspace run only for
    agents. The interactive default workspace serves a person; an agent chat
    inside it is metered separately by its run.
    """
    if row.get("agent_registration_id"):
        return "external_agent"
    if not row.get("is_default"):
        return "automation"
    return "workspace"


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


def new_binding(provider, row=None):
    """A new computer pins provider, sandbox Profile and that Profile's image.

    The owner's install choice selects the Profile; an existing binding is
    never rebuilt to follow a later change.
    """
    selected = settings_profile((row or {}).get("sandbox_settings"))
    # A Profile the default provider does not ship (a desktop on Sprites) goes
    # to a configured provider that does; preflight then validates that one.
    name = profile_provider(selected, provider.name, workspace_providers(provider.name))
    return {
        "provider": name,
        "profile": selected.id,
        "artifact": profile_artifact(selected, provider, name),
    }


def pending_snapshot(rows):
    """(count, age of the oldest request in seconds) for a pending-open listing."""
    now = datetime.now(UTC)
    ages = []
    for row in rows:
        with suppress(KeyError, TypeError, ValueError):
            ages.append((now - datetime.fromisoformat(row["updated_at"])).total_seconds())
    return len(rows), round(max(ages, default=0.0), 1)


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
        self.checked_until = {}
        self.preflight_lock = asyncio.Lock()
        self.active = {}
        # Last poll of this pool's open queue: (pending requests, oldest age in seconds).
        self.pending_snapshot = (0, 0.0)

    def context(self, *args):
        return self.service.context(*args)

    async def preflight(self, row=None, *, connected=False):
        provider = self.providers(None)
        try:
            binding = (
                (row or {}).get("binding") if (row or {}).get("state") != "retired" else None
            ) or new_binding(provider, row)
        except ValueError as exc:
            await provider.aclose()
            raise WorkspaceUnavailable("workbench_provider_not_configured") from exc
        if binding["provider"] not in workspace_providers(provider.name):
            raise WorkspaceUnavailable("workbench_provider_not_configured")
        if binding["provider"] != provider.name:
            provider = self.providers(binding["provider"])
        cache_key = (binding["provider"], binding["artifact"])
        try:
            if not provider.capabilities.shared_workspace or not provider.capabilities.native_files:
                raise ValueError("Workspace capabilities required")
            provider.validate(binding["artifact"], (row or {}).get("resource"))
            async with self.preflight_lock:
                # Existing resources are authenticated and checked by inspect /
                # resume itself. App/image preflight is only an allocation check;
                # repeating it in HTTP and worker adds no authorization fence.
                if (
                    not connected
                    and not (row or {}).get("resource")
                    and time.monotonic() >= self.checked_until.get(cache_key, 0)
                ):
                    async with asyncio.timeout(20):
                        await provider.preflight(binding["artifact"])
                    self.checked_until[cache_key] = time.monotonic() + 30
        except ValueError as exc:
            raise WorkspaceUnavailable("workbench_provider_not_configured") from exc
        except WorkspaceUnavailable:
            raise
        except Exception as exc:
            logger.warning(
                "workbench_provider_connection_failed",
                extra={
                    "dependency_failure": dependency_failure(exc, "workspace_provider"),
                },
            )
            raise WorkspaceUnavailable("workbench_connection_unavailable", retryable=True) from exc
        finally:
            await provider.aclose()
        return provider

    async def ready(self, row):
        async with self.channels.borrow(row) as host:
            await self.filesystem.operation(host, {"action": "list", "path": ""})

    async def request(self, user, project, row=None):
        try:
            return await self._request(user, project, row)
        except Exception as exc:
            if not context_changed(exc):
                raise
        # Provider preflight can take seconds between reading authorization and
        # recording intent. A membership or access change in that window moves
        # the revision; decide once more on fresh facts instead of failing the
        # open. Every step before the recorded intent is safe to repeat.
        return await self._request(user, project, row)

    async def _request(self, user, project, row):
        context, _ = await run_blocking(self.context, user, project, ProjectAction.AGENT_RUN)
        row = (
            row
            or context["workbench"]
            or await run_blocking(self.repository.default, user, project, context["revision"])
        )
        if row["user_id"] != user or row["project_id"] != project:
            raise PermissionError("Workbench ownership mismatch")
        provider = await self.preflight(
            row, connected=row["state"] == "running" and self.channels.is_healthy(row)
        )
        if row["state"] in {"retained", "cleanup_pending"}:
            raise WorkspaceUnavailable("workbench_requires_recovery")
        if row["state"] == "running":
            try:
                row = await run_blocking(self.repository.activity, row, context["revision"])
                async with asyncio.timeout(35):
                    with workspace_stage("opening.files_ready"):
                        await self.ready(row)
                # Recheck the same identity/epoch and current authorization
                # after potentially slow network I/O, before reporting ready.
                await self.check(row)
            except WorkerLost as exc:
                # Compute is gone and the disk is the workspace: maintenance
                # pauses it within one pass, and the retried open recreates
                # compute on the same disk.
                raise WorkspaceUnavailable("workbench_preparing", retryable=True) from exc
            except (WorkerDisconnected, TimeoutError) as exc:
                code, retryable = failure(exc)
                raise WorkspaceUnavailable(code, retryable=retryable) from exc
            return row
        binding = (row["binding"] if row["state"] != "retired" else None) or new_binding(
            provider, row
        )
        return await run_blocking(
            self.repository.request_open,
            row,
            binding,
            context["revision"],
            settings.AGENT_WORKER_POOL,
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
                # The start path readies a Workspace that has no Editor to hand it to.
                if row.get("is_default") and awaiting_files(row):
                    row, _ = await self.service.opening.prepare(
                        run["user_id"], run["project_id"], row["generation"]
                    )
                    break
                await asyncio.sleep(0.25)
                row = await run_blocking(self.repository.get, row["id"])
            await self.check(row)
            await self.ready(row)
            return row

    async def progress(self, row, phase):
        await run_blocking(self.repository.progress, row, phase)

    async def execute(self, original):
        deadline = datetime.fromisoformat(original["open_deadline"])
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            await run_blocking(self.repository.expire_open, original)
            return
        if original["state"] == "pausing":
            return  # The same lifecycle's maintenance finishes pause before wake.
        with workspace_stage("opening.claim"):
            claimed = await run_blocking(self.repository.begin_open, original)
        if not claimed["acquired"]:
            return
        row = claimed["workbench"]

        async def heartbeat():
            while True:
                await asyncio.sleep(30)
                current = await run_blocking(self.repository.get, row["id"])
                if current["operation_id"] != row["operation_id"] or not current["open_requested"]:
                    raise WorkspaceUnavailable("workbench_operation_replaced")
                await self.progress(current, current["operation_phase"])

        async def start():
            async with asyncio.timeout(remaining):
                grant = self.service.authorization.authorize_facts(
                    claimed["facts"], row["project_id"], row["user_id"], ProjectAction.AGENT_RUN
                )
                await self.preflight(row)
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
            self.checked_until.clear()
            current = await run_blocking(self.repository.get, row["id"])
            if (
                current
                and current.get("open_requested")
                and current["operation_id"] == row["operation_id"]
            ):
                with suppress(Exception):
                    await run_blocking(self.repository.progress, current, "failed", code, retryable)
            logger.exception(
                "workbench_start_failed", extra={"workbench_id": row["id"], "code": code}
            )
        finally:
            operation.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(operation, heartbeat_task, return_exceptions=True)
            await self.billing.detach_session(meter_id(row))

    async def serve(self, *, stop=None, grace=0):
        """Execute this pool's pending open requests until ``stop`` is set.

        After stop, in-flight operations get ``grace`` seconds; cancellation
        leaves the durable intent for a successor after its operation lease.
        """

        async def run(row):
            try:
                await self.execute(row)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.checked_until.clear()
                logger.exception("workbench_dispatch_failed", extra={"workbench_id": row["id"]})

        try:
            while stop is None or not stop.is_set():
                try:
                    # Failed credentials are recorded on the claimed operation;
                    # they must not strand every pending request in the queue.
                    provider = self.providers(None)
                    rows = []
                    for name in workspace_providers(provider.name):
                        rows.extend(
                            await run_blocking(
                                self.repository.pending, name, settings.AGENT_WORKER_POOL
                            )
                        )
                    rows.sort(key=lambda row: (row.get("updated_at", ""), row["id"]))
                    self.pending_snapshot = pending_snapshot(rows)
                    for row in rows:
                        if row["id"] not in self.active and len(self.active) < 4:
                            task = asyncio.create_task(run(row))
                            self.active[row["id"]] = task
                            task.add_done_callback(
                                lambda _, identity=row["id"]: self.active.pop(identity, None)
                            )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.checked_until.clear()
                    logger.exception("workbench_provider_preflight_failed")
                    await asyncio.sleep(5)
                await asyncio.sleep(0.5)
            if self.active and grace > 0:
                await asyncio.wait(list(self.active.values()), timeout=grace)
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
                "source": meter_source(row),
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
                target = await run_blocking(
                    self.transport.describe, row["project_id"], grant, target_ref=row["target_ref"]
                )
                source = (
                    target
                    if target["expected_oid"]
                    else await run_blocking(self.transport.describe, row["project_id"], grant)
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
                row = await run_blocking(
                    self.repository.transition, row, "allocating", host.resource
                )
                await host.create()
                row = await run_blocking(
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
            try:
                await host.runtime.resume(host)
            except WorkerLost:
                row = await self._recreate(row, host)
        else:
            raise WorkerDisconnected("Workspace lifecycle transition is not startable")

        async def exchange(frame):
            if "git-receive-pack" in frame.get("path", ""):
                raise PermissionError("Workspace initialization has no publication capability")
            _, current = await run_blocking(
                self.context, row["user_id"], row["project_id"], ProjectAction.CONTENT_READ
            )
            return await self.transport.exchange(
                row["project_id"], current, frame, base=host.resource["git_base"]
            )

        if row["state"] == "allocating":
            host.git_handler = exchange
            await host._open_control()
            await self.progress(row, "connecting")
            capability = await host.control("capabilities")
            validate_runtime(capability.get("runtime"))
            if capability.get("workbench_lifecycle") != 1:
                raise RuntimeError("Workspace artifact does not implement lifecycle initialization")
            await self.progress(row, "initializing")
            await host.control("initialize", host.resource["initialization"])
            # A resource without this mark has no working directory to go back to.
            host.resource["initialized"] = True
            row = await run_blocking(self.repository.transition, row, "allocating", host.resource)
        # Resume is proved by the file service's hello + real root read, on the
        # consuming connection. Do not create two disposable worker SSH channels.
        await self.progress(row, "checking_files")
        if not row.get("is_default"):
            # Automation is consumed in its worker, so its file connection is
            # already owned by that consumer; there is no HTTP handoff.
            await self.ready({**row, "resource": host.resource})
            await run_blocking(
                self.context, row["user_id"], row["project_id"], ProjectAction.AGENT_RUN
            )
            return await run_blocking(self.repository.ready, row, host.resource)
        return row

    async def _recreate(self, row, host):
        """Compute is replaceable; the disk is the workspace.

        The intent is persisted before the provider call, so a lost create reply
        is adopted by exact generation metadata on the next attempt, never
        duplicated. Only a confirmed missing disk retires the generation.
        """
        intent = {**host.resource, "allocation_pending": True, "image_id": host.artifact}
        row = await run_blocking(self.repository.transition, row, "resuming", intent)
        host.resource = intent
        try:
            with workspace_stage("opening.recreate_compute"):
                await host.runtime.recreate(host)
        except WorkspaceDiskLost:
            await run_blocking(self.repository.disk_lost, row)
            logger.warning("workbench_disk_lost", extra={"workbench_id": row["id"]})
            raise WorkspaceUnavailable("workbench_disk_lost", retryable=True) from None
        logger.warning("workbench_compute_recreated", extra={"workbench_id": row["id"]})
        return await run_blocking(self.repository.transition, row, "resuming", host.resource)

    async def destroy(self, row):
        """Destroy compute and disk: the owner was deleted, or the work is on its branch."""
        await self.channels.invalidate(row["id"])
        if row["resource"] is not None:
            host = self.channel_factory(row)
            try:
                await host.runtime.destroy_workspace(row["resource"])
            finally:
                await host.runtime.aclose()
        if settings.RUNTIME_METERING_MODE != "disabled" and not await self.billing.finish_session(
            meter_id(row), retry=True
        ):
            raise WorkerDisconnected("Workspace settlement awaits confirmation")
        await run_blocking(self.repository.transition, row, "retired")
        logger.info("workbench_destroyed", extra={"workbench_id": row["id"]})

    async def _preserve_failed_start(self, row):
        """Bring a failed start to rest, then out of retained once it can be checked.

        Only what cannot be checked stays retained, and every pass reports it.
        """
        host = self.channel_factory(row)
        try:
            if row["resource"] is None or self._never_created(row):
                # Intent is persisted before allocation, so no provider resource exists.
                if row["resource"] is not None and await host.runtime.resolve(host.resource):
                    raise WorkerDisconnected("Allocation outcome requires reconciliation")
                if settings.RUNTIME_METERING_MODE != "disabled":
                    await self.billing.cancel_session(meter_id(row))
                await run_blocking(
                    self.repository.transition,
                    row,
                    "retired" if row["state"] == "retained" else "retained",
                )
                return
            matches = await host.runtime.resolve(host.resource)
            if len(matches) != 1:
                # Keep reconciling a possibly late allocation. Absence after a
                # timeout is not permission to create a second empty workspace.
                logger.error("workspace_requires_operator", extra={"workbench_id": row["id"]})
                raise WorkerDisconnected("Allocation outcome requires reconciliation")
            host.resource = matches[0]
            if row["state"] == "resuming":
                host.resource["initialized"] = True  # Only an initialized disk resumes.
            result = await host.runtime.pause_resource(host)
            host.resource["preservation"] = result.receipt(host.resource)
            if (
                settings.RUNTIME_METERING_MODE != "disabled"
                and not await self.billing.finish_session(meter_id(row), retry=True)
            ):
                raise WorkerDisconnected("Workspace settlement awaits confirmation")
            if row["state"] != "retained":
                await run_blocking(self.repository.transition, row, "retained", host.resource)
            elif host.resource.get("initialized"):
                # The one original resource is confirmed stopped: an ordinary paused disk.
                await run_blocking(self.repository.transition, row, "paused", host.resource)
            else:
                logger.error("workspace_requires_operator", extra={"workbench_id": row["id"]})
        finally:
            await host.runtime.aclose()

    @staticmethod
    def _never_created(row):
        resource = row["resource"] or {}
        return row["state"] == "retained" and bool(resource.get("allocation_pending")) and (
            not resource.get("volume_id")
        )

    async def _attempt_save(self, row):
        """One save with its retries: the status and error to record, and the commit."""
        for attempt in range(SAVE_ATTEMPTS):
            try:
                saved = await self.service.git.snapshot(row)
            except WorkerLost:
                # The disk holds the work, and it stays marked as unsaved.
                return "skipped", "workbench_save_compute_lost", None
            except Exception as exc:
                if access_failure(exc):
                    return "skipped", "workbench_save_no_access", None
                logger.warning(
                    "workspace_save_attempt_failed",
                    extra={"workbench_id": row["id"], "attempt": attempt + 1},
                )
                if attempt + 1 < SAVE_ATTEMPTS:
                    await asyncio.sleep(SAVE_RETRY_SECONDS)
                continue
            if saved["status"] == "saved":
                return "saved", None, saved.get("tip")
            if saved["status"] == "merging":
                return "merging", "workbench_save_merging", None
            return "rejected", "workbench_save_rejected", None
        return "failed", "workbench_save_failed", None

    async def _save_agent_work(self, row):
        """An external Agent's branch is the only way its work leaves the machine.

        This runs while pausing, when no new connection can start. A pause keeps the
        disk, and unsaved work is never reclaimed, so a failed save loses nothing and
        cannot hold the pause: the machine sleeps, the Agent can come back, and the
        result is recorded for its owner.
        """
        if not row.get("agent_registration_id"):
            return
        try:
            async with asyncio.timeout(SAVE_BUDGET_SECONDS):
                status, error, oid = await self._attempt_save(row)
        except TimeoutError:
            status, error, oid = "failed", "workbench_save_timeout", None
        if status != "saved":
            logger.error(
                "workspace_save_incomplete",
                extra={"workbench_id": row["id"], "save_status": status, "save_error": error},
            )
        await run_blocking(self.repository.save_outcome, row, status, error=error, oid=oid)
        await self.progress(row, "pausing")  # The save may have used most of the lease.

    async def pause(self, row):
        await self._save_agent_work(row)
        await self.channels.invalidate(row["id"])
        host = self.channel_factory(row)
        try:
            result = await host.runtime.pause_resource(host)
            host.resource["preservation"] = result.receipt(host.resource)
        except WorkspaceDiskLost:
            await run_blocking(self.repository.disk_lost, row)
            logger.error("workbench_disk_lost", extra={"workbench_id": row["id"]})
            return
        finally:
            await host.runtime.aclose()
        # Settlement precedes paused ACK and the next compute epoch.
        if settings.RUNTIME_METERING_MODE != "disabled" and not await self.billing.finish_session(
            meter_id(row), retry=True
        ):
            raise WorkerDisconnected("Workspace settlement awaits confirmation")
        paused = await run_blocking(self.repository.transition, row, "paused", host.resource)
        await run_blocking(self.repository.progress, paused, "paused")

    @staticmethod
    def _pause_expired(row):
        started = row.get("pausing_since")
        if not started:
            return False
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
        return elapsed >= PAUSE_LIMIT_SECONDS

    async def _abandon_pause(self, row):
        """A pause that cannot finish ends: back to serving, or held for a check."""
        logger.error("workspace_pause_abandoned", extra={"workbench_id": row["id"]})
        host = self.channel_factory(row)
        try:
            await host.runtime.running_connection(host)
        except Exception:
            await run_blocking(self.repository.transition, row, "retained")
        else:
            running = await run_blocking(self.repository.transition, row, "running")
            await run_blocking(self.repository.progress, running, "ready")
        finally:
            await host.runtime.aclose()

    async def reap(self):
        for name in workspace_providers(self.providers(None).name):
            try:
                await self._reap_provider(name)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("workbench_provider_maintenance_failed", extra={"provider": name})

    async def _reap_provider(self, name):
        for row in await run_blocking(self.repository.due, name, settings.AGENT_WORKER_POOL):
            if (
                row.get("open_requested")
                and row["state"] != "pausing"
                and not row.get("owner_deleted_at")
            ):
                continue
            owned = None
            try:
                await self.preflight(row, connected=True)
                if row.get("owner_deleted_at"):
                    # Deletion means deletion: no restore path exists for a
                    # deleted Project or user, so nothing is preserved.
                    if row["state"] in {"retained", "cleanup_pending"}:
                        claimed = await run_blocking(self.repository.reclaim_claim, row)
                        if claimed["acquired"]:
                            owned = claimed["workbench"]
                            await self.destroy(owned)
                    continue
                if row["state"] == "cleanup_pending":
                    # A claimed reclaim that failed: its work is saved, so it is retried.
                    claimed = await run_blocking(self.repository.reclaim_claim, row)
                    if claimed["acquired"]:
                        owned = claimed["workbench"]
                        await self.destroy(owned)
                    continue
                if row["state"] == "paused":
                    # Saved Agent work is on its branch; the machine can be rebuilt from it.
                    if row.get("agent_registration_id"):
                        claimed = await run_blocking(self.repository.retire, row)
                        if claimed["acquired"]:
                            owned = claimed["workbench"]
                            await self.destroy(owned)
                    continue
                if row["state"] == "running":
                    claimed = await run_blocking(self.repository.idle, row)
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
                        # Revoked access and lost compute both end the session.
                        # Pausing lost compute verifies the disk survives; the
                        # next open recreates compute on it.
                        if not (access_failure(exc) or isinstance(exc, WorkerLost)):
                            raise
                        claimed = await run_blocking(
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
                    claimed = await run_blocking(self.repository.lifecycle_claim, row)
                    if claimed["acquired"]:
                        current = owned = claimed["workbench"]
                        if current["state"] == "pausing" and self._pause_expired(current):
                            await self._abandon_pause(current)
                        elif current["state"] == "pausing":
                            await self.pause(current)
                        else:
                            await self._preserve_failed_start(current)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                current = await run_blocking(self.repository.get, row["id"])
                if owned and current and current.get("operation_id") == owned["operation_id"]:
                    with suppress(Exception):
                        code, retryable = failure(exc)
                        await run_blocking(
                            self.repository.progress, current, "failed", code, retryable
                        )
                logger.exception("workbench_maintenance_retry", extra={"workbench_id": row["id"]})
