"""Run-bound Git synchronization and publication; no project bytes or object codecs."""

import asyncio

from src.platform.access.adapters.agent.runtime.ports import GitTransport
from src.platform.access.adapters.agent.runtime.workspace import (
    require_full_project,
    workspace_state,
)
from src.platform.project.write_lease import ProjectWriteLease
from src.version_engine.adapters.git.run_transport import RunPublication
from src.version_engine.read.ref_metadata import GitBranchBase


class PublicationRejected(ValueError):
    """A preflight rejected the candidate before publication."""


def actor(run):
    return f"cloud-agent:{run['id']}:{run['execution_id']}:{run['fence']}"


class Publication:
    def __init__(self, transport: GitTransport):
        self.transport = transport

    @staticmethod
    def target(run):
        if not run.get("workbench_id"):
            return None
        target = run["policy"].get("workbench_target_ref")
        if not isinstance(target, str) or not target.startswith("refs/heads/"):
            raise PermissionError("Workbench publication target is not admitted")
        return target

    def describe(self, run, grant):
        target = self.target(run)
        options = {"target_ref": target} if target is not None else {}
        return self.transport.describe(run["project_id"], grant, **options)

    def validate_target(self, run, value):
        target = self.target(run)
        if target is not None:
            base = GitBranchBase.parse(value["base"])
            if (base.ref_name.decode() != target or base.head_guard is not None
                    or value["workspace"]["target_ref"] != target):
                raise PermissionError("Workbench publication target changed")

    def capture(self, run, grant):
        require_full_project(run)
        value = workspace_state(run, self.describe(run, grant))
        self.validate_target(run, value)
        return value

    async def exchange(self, run, value, grant, frame):
        self.validate_target(run, value)
        publishing = "git-receive-pack" in frame.get("path", "")
        if not publishing:
            return await self.transport.exchange(
                run["project_id"], grant, frame, base=value["base"]
            )
        candidate = value["workspace"].get("tip")
        if run["state"] != "publishing" or run["policy"]["readonly"] or not candidate:
            raise PermissionError("Run has no publication capability")
        binding = RunPublication(run["id"], GitBranchBase.parse(value["base"]), candidate)
        if frame["method"] == "GET":
            return await self.transport.exchange(
                run["project_id"], grant, frame, base=value["base"], publication=binding
            )
        lease = ProjectWriteLease(run["project_id"], "agent.git.push", reuse_active=False)
        lease.holder_id = actor(run)
        async with lease:
            return await self.transport.exchange(
                run["project_id"], grant, frame, base=value["base"], publication=binding
            )

    async def publish(self, run, checkpoint, grant, worker):
        self.validate_target(run, checkpoint)
        state = checkpoint["workspace"]
        if not state.get("changed"):
            return {"status": "no_changes"}
        if run["policy"]["readonly"]:
            raise PublicationRejected("Read-only Agent cannot publish")
        result = await asyncio.to_thread(self.transport.result, run["project_id"], grant, run["id"])
        if result is not None and result["status"] == "pending":
            raise RuntimeError("Original Git publication is still pending")
        if result is None:
            current = await asyncio.to_thread(self.describe, run, grant)
            if GitBranchBase.parse(current) != GitBranchBase.parse(checkpoint["base"]):
                return {
                    "status": "conflict",
                    "commit_id": state["tip"],
                    "target_ref": state["target_ref"],
                    "code": "cloud_branch_changed",
                }
            try:
                pushed = await worker.control("push", {"candidate": state["tip"]})
            except Exception:
                # ACK loss must be reconciled by original identity, never by current HEAD.
                result = await asyncio.to_thread(
                    self.transport.result, run["project_id"], grant, run["id"]
                )
                if result is None:
                    raise
            else:
                result = await asyncio.to_thread(
                    self.transport.result, run["project_id"], grant, run["id"]
                )
                if result is None and pushed.get("rejected"):
                    return {
                        "status": "conflict",
                        "commit_id": state["tip"],
                        "target_ref": state["target_ref"],
                        "code": "git_push_rejected",
                    }
        if result is None or result["status"] == "pending":
            raise RuntimeError("Git publication outcome unavailable")
        return {**result, "commit_id": state["tip"], "target_ref": state["target_ref"]}
