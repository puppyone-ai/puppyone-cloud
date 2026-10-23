"""Link conversation/receipts to immutable provider recovery material."""

from src.platform.workspace.filesystem.port import FilesystemPort
from src.platform.workspace.execution.worker_port import WorkspaceWorker


class Recovery:
    def __init__(self, filesystem: FilesystemPort):
        self.filesystem = filesystem

    async def after_tool(self, worker: WorkspaceWorker, value, frame):
        # Unknown tools and failed bash/edit calls can still have changed files.
        mutating = frame["name"] not in {"read", "ls", "find", "grep"}
        result = {**value, "reason": "after_tool"}
        if mutating:
            result["recovery"] = await self.filesystem.snapshot(worker)
        return result

    async def finalize(self, worker: WorkspaceWorker, value, run):
        state = await self.filesystem.finalize(worker, "Agent run " + run["id"])
        result = {**value, "workspace": {**value["workspace"], **state}}
        if state["changed"]:
            result["recovery"] = await self.filesystem.snapshot(worker)
        return result
