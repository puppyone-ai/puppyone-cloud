"""Workspace Git outside a Run: everything that crosses between branches.

Workspaces only ever exchange Git branches. Two operations cover every direction:

- ``sync`` brings a branch into a running Workspace. The Project's branch keeps
  a Workspace current; an Agent branch is how its work is reviewed.
- ``publish`` moves the Workspace's work onto the Project's branch.

``save`` commits the working files and pushes them to the Workspace's own branch,
which is all an external Agent's Workspace ever publishes. Merging runs in the
sandbox as stock Git, so a conflict is left as marked files in the Workspace. Every
write goes through the same Git admission, ref CAS and receipts as a Run.
"""

from contextlib import suppress
from uuid import uuid4

from src.infra.execution.runner import run_blocking
from src.platform.authorization.models import ProjectAction
from src.platform.project.write_lease import ProjectWriteLease

from .errors import WorkspaceError

SAVE_MESSAGE = "Save workspace changes"
SYNC_MESSAGE = "Bring in {source}"


class WorkspaceGit:
    def __init__(self, service):
        self.service = service

    @property
    def transport(self):
        return self.service.transport

    async def _grant(self, row, action=ProjectAction.CONTENT_WRITE):
        _, grant = await run_blocking(self.service.context, row["user_id"], row["project_id"], action)
        return grant

    async def _describe(self, row, grant, target_ref=None):
        options = {"target_ref": target_ref} if target_ref else {}
        return await run_blocking(self.transport.describe, row["project_id"], grant, **options)

    def _reader(self, row, base):
        async def exchange(frame):
            if "git-receive-pack" in frame.get("path", ""):
                raise PermissionError("This Workspace operation has no publication capability")
            grant = await self._grant(row, ProjectAction.CONTENT_READ)
            return await self.transport.exchange(row["project_id"], grant, frame, base=base)

        return exchange

    def _writer(self, row, base, binding):
        project = row["project_id"]

        async def exchange(frame):
            grant = await self._grant(row)
            if "git-receive-pack" not in frame.get("path", ""):
                return await self.transport.exchange(project, grant, frame, base=base)
            if frame["method"] == "GET":
                return await self.transport.exchange(
                    project, grant, frame, base=base, publication=binding
                )
            lease = ProjectWriteLease(project, "workspace.git.push", reuse_active=False)
            lease.holder_id = f"workspace:{row['id']}:{binding.request_key}"
            async with lease:
                return await self.transport.exchange(
                    project, grant, frame, base=base, publication=binding
                )

        return exchange

    async def _operate(self, row, base, action, value, exchange):
        host = self.service.channel_factory(row)
        host.git_handler = exchange
        try:
            await host.connect(await self.service.connection(row))
            capability = await host.control("capabilities")
            if capability.get("workspace_git") != 1:
                raise WorkspaceError(409, {"code": "workspace_capability_missing"})
            config = {
                "project_id": row["project_id"],
                "object_format": base["object_format"],
                "target_ref": row["target_ref"],
            }
            return await host.control(
                "workspace-git", {"action": action, "config": config, "value": value}
            )
        finally:
            with suppress(Exception):
                await host.close()

    async def _require_quiet(self, row):
        # Files are about to change under the worktree; a task must not be mid-edit.
        if await run_blocking(self.service.repository.has_active_runs, row):
            raise WorkspaceError(409, {"code": "workspace_busy"})

    async def _push(self, row, grant, base, candidate):
        binding = self.transport.publication(str(uuid4()), base, candidate)
        pushed = await self._operate(
            row,
            base,
            "publish",
            {"destination_ref": base["target_ref"]},
            self._writer(row, base, binding),
        )
        if pushed["status"] != "pushed":
            return pushed["status"]
        result = await run_blocking(
            self.transport.result, row["project_id"], grant, binding.request_key
        )
        if result is None or result["status"] == "pending":
            raise RuntimeError("Workspace publication outcome unavailable")
        return "pushed" if result["status"] == "committed" else result["status"]

    async def save(self, row):
        """Commit the working files and move the Workspace's own branch to them."""
        grant = await self._grant(row)
        base = await self._describe(row, grant, row["target_ref"])
        saved = await self._operate(
            row, base, "save", {"message": SAVE_MESSAGE}, self._reader(row, base)
        )
        if saved["status"] == "merging":
            return {"status": "merging", "tip": saved.get("tip")}
        tip = saved["tip"]
        if tip is None or tip == base["expected_oid"]:
            return {"status": "saved", "tip": tip}
        status = await self._push(row, grant, base, tip)
        return {"status": "saved" if status == "pushed" else status, "tip": tip}

    async def sync(self, row, source_ref=None):
        """Bring a branch into the Workspace; the default is the Project's branch."""
        await self._require_quiet(row)
        grant = await self._grant(row)
        project = await self._describe(row, grant)
        source = source_ref or project["target_ref"]
        if not source.startswith("refs/heads/"):
            raise WorkspaceError(422, {"code": "workspace_sync_source_invalid"})
        base = await self._describe(row, grant, row["target_ref"])
        value = {
            "source_ref": source,
            "message": SYNC_MESSAGE.format(source=source.removeprefix("refs/heads/")),
        }
        return await self._operate(row, base, "sync", value, self._reader(row, base))

    async def finish(self, row):
        """Commit a resolved merge."""
        await self._require_quiet(row)
        grant = await self._grant(row)
        base = await self._describe(row, grant, row["target_ref"])
        value = {"message": "Finish bringing in changes"}
        return await self._operate(row, base, "sync-finish", value, self._reader(row, base))

    async def abort(self, row):
        """Drop a waiting merge; work saved before it started stays."""
        await self._require_quiet(row)
        grant = await self._grant(row)
        base = await self._describe(row, grant, row["target_ref"])
        return await self._operate(row, base, "sync-abort", {}, self._reader(row, base))

    async def publish(self, row):
        """Save, then move the Project's branch to the Workspace's work."""
        await self._require_quiet(row)
        saved = await self.save(row)
        if saved["status"] != "saved":
            return saved
        grant = await self._grant(row)
        project = await self._describe(row, grant)
        tip = saved["tip"]
        if tip is None or tip == project["expected_oid"]:
            return {"status": "no_changes", "tip": tip}
        status = await self._push(row, grant, project, tip)
        return {"status": "published" if status == "pushed" else status, "tip": tip}
