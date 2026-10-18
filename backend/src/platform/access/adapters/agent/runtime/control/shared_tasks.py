"""Run-owned tasks on a lifecycle-owned computer, using the durable task ledger."""

import asyncio
from typing import Any, Protocol

from src.platform.scope_sandbox.execution.workbench_task import WorkbenchTask


class WorkbenchOwner(Protocol):
    """Resource admission/metering is mandatory and independent of Run ownership.

    The production dispatcher must not enable shared claims without this owner.
    connection() returns an already-running authorized connection; it cannot
    silently create/resume compute while cleaning a task.
    """

    async def ensure_running(self, run: dict, workbench: dict, *, base: dict) -> Any: ...
    async def connection(self, workbench: dict) -> Any: ...
    async def check(self, workbench: dict) -> None: ...
    async def reap(self) -> None: ...


class SharedTasks:
    def __init__(self, repository, owner: WorkbenchOwner, *, worker_factory=WorkbenchTask):
        if owner is None:
            raise ValueError("A shared workbench lifecycle owner is required")
        self.repository, self.owner, self.worker_factory = repository, owner, worker_factory
        self.row = None

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
        self.validate_checkpoint(row, value)
        await self.owner.ensure_running(run, row, base=value["base"])
        # The owner returns no substitute authority: read back its durable ACK.
        self.row = await self.workbench(run)
        identity = self.validate_checkpoint(self.row, value)
        await self.owner.check(self.row)
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
