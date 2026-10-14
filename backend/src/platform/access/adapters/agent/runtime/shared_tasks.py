"""Run-owned tasks on a lifecycle-owned computer, using the durable task ledger."""

import asyncio
from typing import Any, Protocol

from src.platform.access.adapters.agent.runtime.shared_recovery import SharedRecovery
from src.platform.scope_sandbox.execution.workbench_task import WorkbenchTask
from src.platform.scope_sandbox.execution.worker_port import WorkerLost


class WorkbenchHost(Protocol):
    """Borrow a private user's already-running workspace; never provision for Chat.

    Creation, initialization and wake belong to workspace lifecycle operations.
    check() verifies current authorization/resource/metering, not readiness repair.
    connection() must neither create nor wake compute, including during cleanup.
    """

    async def connection(self, workbench: dict) -> Any: ...
    async def check(self, workbench: dict) -> None: ...


class WorkbenchOwner(WorkbenchHost, Protocol):
    """The dispatcher's lifecycle owner; task orchestration only needs its host view."""

    async def reap(self) -> None: ...


class SharedTasks:
    def __init__(self, repository, owner: WorkbenchHost, *, worker_factory=WorkbenchTask):
        if owner is None:
            raise ValueError("A shared workbench lifecycle owner is required")
        self.repository, self.owner, self.worker_factory = repository, owner, worker_factory
        self.row = None
        self.recovery = SharedRecovery()

    async def workbench(self, run):
        row = await asyncio.to_thread(self.repository.workbench, run["workbench_id"])
        if not row or row["user_id"] != run["user_id"] or row["project_id"] != run["project_id"]:
            raise PermissionError("Shared workbench ownership mismatch")
        return row

    @staticmethod
    def validate_checkpoint(row, value):
        identity = {"id": row["id"], "generation": row["generation"]}
        if value.get("recovery") is not None or value.get("workbench", identity) != identity:
            raise PermissionError("Shared checkpoint cannot restore or replace live files")
        if value["base"]["target_ref"] != row["target_ref"]:
            raise PermissionError("Workbench ref publication contract is unavailable")
        return identity

    async def acquire(self, run, value):
        row = await self.workbench(run)
        identity = self.validate_checkpoint(row, value)
        if row["state"] != "running" or not (row.get("resource") or {}).get("resource_id"):
            raise WorkerLost("Workbench must already be running before task admission")
        self.row = row
        await self.owner.check(row)
        # SQL acquisition revalidates state/generation and occupancy atomically.
        # A Chat must never repair this check by allocating/resuming a machine.
        bound = await asyncio.to_thread(self.repository.acquire, run, self.row["generation"])
        worker = await self.connect(run, self.row, bound)
        return worker, {**value, "workbench": identity, "recovery": None}

    async def connect(self, run, row, bound):
        if (bound["run_id"] != run["id"] or bound["workbench_id"] != row["id"]
                or bound["generation"] != row["generation"] or bound["fence"] > run["fence"]):
            raise PermissionError("Shared task cleanup binding mismatch")
        resource = bound["resource"]
        if (resource.get("task_execution_id") != bound["execution_id"]
                or resource.get("task_uid") != bound["task_uid"]
                or resource.get("workbench_id") != row["id"]
                or resource.get("workbench_generation") != row["generation"]
                or resource.get("project_id") != run["project_id"]
                or resource.get("provider") != row["binding"].get("provider")
                or resource.get("resource_id") != (row["resource"] or {}).get("resource_id")):
            raise PermissionError("Shared task resource mismatch")
        connection = await self.owner.connection(row)
        worker = self.worker_factory(bound["execution_id"], run["project_id"],
            workbench_id=row["id"], generation=row["generation"], task_uid=bound["task_uid"],
            provider=resource["provider"], image=row["binding"].get("artifact"))
        worker.restore_point = None
        await worker.connect(resource, sandbox=connection)
        return worker

    async def check(self):
        if self.row is not None:
            await self.owner.check(self.row)

    async def close(self, run, worker=None, *, predecessors_only=False):
        for bound in await asyncio.to_thread(self.repository.active, run):
            if predecessors_only and bound["execution_id"] == run["execution_id"]:
                continue
            current = worker if worker and worker.task_execution_id == bound["execution_id"] else None
            if current is None:
                current = await self.connect(run, await self.workbench(run), bound)
            if current.resource != bound["resource"] or current.task_uid != bound["task_uid"]:
                raise PermissionError("Connected task differs from its durable cleanup identity")
            await current.stop()
            # Never release on timeout/lease expiry or merely on a missing handle.
            # An ambiguous stop leaves the exact identity for fenced takeover.
            await asyncio.to_thread(self.repository.released, run, bound)
