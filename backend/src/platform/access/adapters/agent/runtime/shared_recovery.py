"""Recover conversation progress, never rewind a shared computer's live files."""


class SharedRecovery:
    @staticmethod
    def validate(worker, value):
        expected = {"id": worker.resource["workbench_id"],
                    "generation": worker.resource["workbench_generation"]}
        if value.get("workbench") != expected or value.get("recovery") is not None:
            raise PermissionError("Shared conversation/workbench binding mismatch")

    async def after_tool(self, worker, value, frame, intent):
        self.validate(worker, value)
        # Failed/unknown tools can have written files too. Those files remain in
        # the live workbench; the tool ledger, not a filesystem rollback, decides
        # whether conversation execution may continue.
        return {**value, "reason": "after_tool", "recovery": None}

    async def finalize(self, worker, run, value):
        self.validate(worker, value)
        if value.get("publication_candidate") not in (None, run["id"]):
            raise PermissionError("Publication candidate belongs to another Run")
        if value.get("publication_candidate") == run["id"]:
            # Preserve the original publication candidate across a lost ACK.
            # Another Session may already have edited/committed the shared tree.
            return value
        state = await worker.control("finalize", {"message": f"Agent checkpoint: run {run['id']}"})
        return {**value, "workspace": {**value["workspace"], **state},
                "reason": "settled", "publication_candidate": run["id"], "recovery": None}
