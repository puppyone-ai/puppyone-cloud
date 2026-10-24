"""Session workspace ownership. SQL fences state; provider adapters own compute."""

import asyncio
import logging

from src.platform.workspace.filesystem.port import FilesystemPort
from src.platform.workspace.execution.worker_port import WorkerLost
from src.config import settings
from src.infra.execution.runner import run_blocking

logger = logging.getLogger(__name__)


class SessionWorkspace:
    def __init__(self, repository, filesystem: FilesystemPort):
        self.repository, self.filesystem = repository, filesystem
        self.row = None

    async def worker(self, run, factory):
        # A deployment default only selects NEW resources. An existing Session
        # keeps its provider and artifact even after switching the default.
        row = await run_blocking(self.repository.workspace, run["session_id"])
        if row and row["binding"]:
            binding, resource = row["binding"], row["resource"] or {}
            return factory(
                run["execution_id"],
                run["project_id"],
                provider=binding["provider"],
                image=resource.get("image_id", binding["artifact"]),
                artifact=binding["artifact"],
            )
        return factory(run["execution_id"], run["project_id"])

    async def transition(self, run, state, resource=None):
        self.row = await run_blocking(
            self.repository.workspace_transition, run, self.row, state, resource
        )

    async def acquire(self, run, value, worker):
        binding = {key: value["base"][key] for key in ("generation", "object_format", "target_ref")}
        binding.update(provider=worker.provider, artifact=worker.artifact)
        async with asyncio.timeout(180):
            while True:
                try:
                    self.row = await run_blocking(
                        self.repository.workspace_acquire, run, binding
                    )
                    break
                except Exception as exc:
                    if getattr(exc, "message", "") != "agent_workspace_busy":
                        raise
                    await asyncio.sleep(0.5)
        if self.row["state"] == "cleanup_pending":
            if self.row["resource"]:
                await worker.cleanup(self.row["resource"], store=worker.store)
            await self.transition(run, "retired")
            self.row = await run_blocking(self.repository.workspace_acquire, run, binding)
        if self.row["resource"] and self.row["state"] == "resuming":
            try:
                await worker.resume(self.row["resource"])
            except WorkerLost:
                # A confirmed absent/incompatible resource is cold reconstruction,
                # never a silent fallback on a provider timeout or permission error.
                await self.transition(run, "cleanup_pending")
                await worker.cleanup(self.row["resource"], store=worker.store)
                await self.transition(run, "retired")
                self.row = await run_blocking(self.repository.workspace_acquire, run, binding)
            else:
                await self.transition(run, "running", worker.resource)
                return dict(worker.resource)
        worker.resource.update(
            workspace_id=run["session_id"], workspace_generation=self.row["generation"]
        )
        # Persist the provider allocation intent before create, including its
        # exact metadata/name for recovery from a lost allocation response.
        if self.row["resource"] is None:
            await self.transition(run, "allocating", worker.resource)
        self.filesystem.bind_recovery(
            worker,
            value.get("recovery")
            if run["checkpoint"] and value.get("reason") != "prepared"
            else None,
        )
        resource = await worker.create()
        await self.transition(run, "running", resource)
        return resource

    async def pause(self, run, worker):
        if self.row["state"] == "paused":
            return
        if self.row["state"] != "pausing":
            await self.transition(run, "pausing")
        await worker.pause()
        await self.transition(run, "paused", worker.resource)

    async def dispose(self, run, worker, *, retain=False):
        if not self.row:
            return
        if self.row["state"] == "retired":
            return
        if retain:
            await self.transition(run, "retained")
            return
        await self.transition(run, "cleanup_pending")
        await worker.stop()
        await self.transition(run, "retired")


async def reap_workspaces(repository, lifecycle, *, pool=None):
    """Bounded indexed claims. Repeated kill is safe; uncertain ACK stays owned.

    Only Session workspaces of this worker's pool are retired.
    """
    retired = 0
    for row in await run_blocking(
        repository.workspace_due, pool=pool or settings.AGENT_WORKER_POOL
    ):
        try:
            if row["resource"]:
                await lifecycle.cleanup(row["resource"])
            retired += bool(await run_blocking(repository.workspace_retired, row))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "agent_workspace_retirement_retry", extra={"session_id": row["session_id"]}
            )
    return retired


def allowed(path, policy):
    prefix = policy["path_prefix"]
    return (not prefix or path == prefix or path.startswith(prefix + "/")) and not any(
        path == excluded or path.startswith(excluded + "/") for excluded in policy["excludes"]
    )


def require_full_project(run):
    policy = run["policy"]
    if (
        run.get("scope_id")
        or policy["path_prefix"]
        or policy["excludes"]
        or not policy["materialize"]
    ):
        raise ValueError("Agent Git requires an unrestricted Project-root view")


def workspace_state(run, base):
    require_full_project(run)
    target = base.get("target_ref")
    if not isinstance(target, str) or not target.startswith("refs/heads/"):
        raise ValueError("Agent Git requires a selected cloud branch")
    return {
        "base": base,
        "reason": "prepared",
        "recovery": None,
        "workspace": {
            "object_format": base["object_format"],
            "target_ref": target,
            "base_oid": base["expected_oid"],
            "readonly": run["policy"]["readonly"],
        },
    }
