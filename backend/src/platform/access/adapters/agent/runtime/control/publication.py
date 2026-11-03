"""Run-bound Git synchronization and publication; no project bytes or object codecs."""


from src.infra.execution.runner import run_blocking
from src.platform.access.adapters.agent.runtime.control.ports import GitTransport
from src.platform.access.adapters.agent.runtime.control.workspace import (
    require_full_project,
    workspace_state,
)
from src.platform.access.adapters.git.workspace_transport import RunPublication
from src.platform.project.write_lease import ProjectWriteLease
from src.platform.workspace.filesystem.port import FilesystemPort
from src.version_engine.read.ref_metadata import GitBranchBase


# Overlapping tasks in one Workspace can race for the same ref; each retry re-reads it.
PUSH_ATTEMPTS = 3


class PublicationRejected(ValueError):
    """A preflight rejected the candidate before publication."""


def actor(run):
    return f"cloud-agent:{run['id']}:{run['execution_id']}:{run['fence']}"


def same_branch(first, second):
    """Whether two ref descriptions differ only in where the branch now points."""
    first, second = GitBranchBase.parse(first), GitBranchBase.parse(second)
    return (
        first.object_format == second.object_format
        and first.generation == second.generation
        and first.ref_name == second.ref_name
        and first.head_guard == second.head_guard
    )


def outcome(state, status, code=None):
    value = {"status": status, "commit_id": state["tip"], "target_ref": state["target_ref"]}
    return {**value, "code": code} if code else value


class Publication:
    def __init__(self, transport: GitTransport, filesystem: FilesystemPort):
        self.transport = transport
        self.filesystem = filesystem

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
        result = await run_blocking(self.transport.result, run["project_id"], grant, run["id"])
        if result is not None and result["status"] == "pending":
            raise RuntimeError("Original Git publication is still pending")
        if result is None:
            shared = self.target(run) is not None
            admitted = checkpoint["base"]
            for attempt in range(PUSH_ATTEMPTS):
                current = await run_blocking(self.describe, run, grant)
                remote_tip = None
                if GitBranchBase.parse(current) != GitBranchBase.parse(checkpoint["base"]):
                    if not shared or not same_branch(current, checkpoint["base"]):
                        return outcome(state, "conflict", "cloud_branch_changed")
                    # Tasks in one Workspace share a worktree, so another task's
                    # publication advances this Run's branch without diverging
                    # from it. Git and the host still refuse a non-fast-forward.
                    remote_tip = current["expected_oid"]
                    if remote_tip == state["tip"]:
                        return outcome(state, "no_changes")
                    checkpoint["base"] = current
                try:
                    pushed = await self.filesystem.push(
                        worker, state["tip"], remote_tip=remote_tip
                    )
                except Exception:
                    # ACK loss must be reconciled by original identity, never by current HEAD.
                    result = await run_blocking(
                        self.transport.result, run["project_id"], grant, run["id"]
                    )
                    if result is None:
                        raise
                    break
                result = await run_blocking(
                    self.transport.result, run["project_id"], grant, run["id"]
                )
                if result is not None:
                    break
                if pushed.get("contained"):
                    return outcome(state, "no_changes")
                if not pushed.get("rejected"):
                    break
                # A refused push is final unless a parallel task moved the branch.
                moved = await run_blocking(self.describe, run, grant) if shared else current
                if moved == current or attempt == PUSH_ATTEMPTS - 1:
                    checkpoint["base"] = admitted
                    return outcome(state, "conflict", "git_push_rejected")
        if result is None or result["status"] == "pending":
            raise RuntimeError("Git publication outcome unavailable")
        return {**result, "commit_id": state["tip"], "target_ref": state["target_ref"]}
