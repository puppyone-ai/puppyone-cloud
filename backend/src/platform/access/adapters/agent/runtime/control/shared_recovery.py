"""Recover conversation progress, never rewind a shared computer's live files."""

from src.platform.access.adapters.agent.runtime.execution.port import ExecutionPort
from src.platform.workspace.filesystem.port import FilesystemPort


class SharedRecovery:
    def __init__(self, execution: ExecutionPort, filesystem: FilesystemPort):
        self.execution, self.filesystem = execution, filesystem

    @staticmethod
    def publication_ready(value, run):
        return (value.get("reason") == "settled" and value.get("publication_candidate") == run["id"]
                and (value.get("workbench") or {}).get("id") == run["workbench_id"]
                and value.get("recovery") is None)

    @staticmethod
    def validate(worker, value):
        expected = {"id": worker.resource["workbench_id"],
                    "generation": worker.resource["workbench_generation"]}
        if value.get("workbench") != expected or value.get("recovery") is not None:
            raise PermissionError("Shared conversation/workbench binding mismatch")

    async def after_tool(self, worker, value, frame):
        self.validate(worker, value)
        # Failed/unknown tools can have written files too. Those files remain in
        # the live workbench; the tool ledger, not a filesystem rollback, decides
        # whether conversation execution may continue.
        return {**value, "reason": "after_tool", "recovery": None}

    async def finalize(self, worker, value, run):
        self.validate(worker, value)
        if value.get("publication_candidate") not in (None, run["id"]):
            raise PermissionError("Publication candidate belongs to another Run")
        if value.get("publication_candidate") == run["id"]:
            # Preserve the original publication candidate across a lost ACK.
            # Another Session may already have edited/committed the shared tree.
            await self.execution.quiesce(worker, shared=True)
            return value
        state = await self.filesystem.finalize(worker, f"Agent checkpoint: run {run['id']}")
        return {**value, "workspace": {**value["workspace"], **state},
                "reason": "settled", "publication_candidate": run["id"], "recovery": None}
