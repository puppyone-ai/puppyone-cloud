"""Workspace Git outside a Run: everything that crosses between branches.

Workspaces only ever exchange Git branches. Two operations cover every direction:

- ``sync`` brings a branch into a running Workspace. The Project's branch keeps
  a Workspace current; an Agent branch is how its work is reviewed.
- ``publish`` moves the Workspace's work onto the Project's branch.

``save`` commits the working files and pushes them to the Workspace's own branch.
``snapshot`` does the same for an external Agent without using its Git state, which
is all that Workspace ever publishes. Merging runs in the
sandbox as stock Git, so a conflict is left as marked files in the Workspace. Every
write goes through the same Git admission, ref CAS and receipts as a Run.
"""

import logging
from contextlib import suppress
from uuid import uuid4

from src.infra.execution.runner import run_blocking
from src.platform.project.write_lease import ProjectWriteLease
from src.platform.repository_policy import WriteChannel, get_repository_policy, require_write
from src.shared_types.grants import ProjectAction

from .errors import WorkspaceError

logger = logging.getLogger(__name__)

SAVE_MESSAGE = "Save workspace changes"
BACKUP_MESSAGE = "Workspace backup"
SYNC_MESSAGE = "Bring in {source}"
# A sync after which the Workspace holds the source branch's tip.
SYNCED = frozenset({"up_to_date", "fast_forward", "merged"})
# The sandbox artifact that can save without using a task's HEAD and index.
SNAPSHOT_PROTOCOL = 2


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
            require_write(WriteChannel.WORKSPACE)
            lease = ProjectWriteLease(project, "workspace.git.push", reuse_active=False)
            lease.holder_id = f"workspace:{row['id']}:{binding.request_key}"
            async with lease:
                return await self.transport.exchange(
                    project, grant, frame, base=base, publication=binding
                )

        return exchange

    async def _operate(self, row, base, action, value, exchange, *, protocol=1, target_ref=None):
        host = self.service.channel_factory(row)
        host.git_handler = exchange
        try:
            # An Agent's work is saved while its Workspace is pausing, not after.
            await host.connect(
                await self.service.connection(row, states=("running", "pausing"))
            )
            capability = await host.control("capabilities")
            if (capability.get("workspace_git") or 0) < protocol:
                raise WorkspaceError(409, {"code": "workspace_capability_missing"})
            config = {
                "project_id": row["project_id"],
                "object_format": base["object_format"],
                "target_ref": target_ref or row["target_ref"],
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

    async def _push(
        self,
        row,
        grant,
        base,
        candidate,
        *,
        action="publish",
        value=None,
        protocol=1,
        target_ref=None,
    ):
        binding = self.transport.publication(str(uuid4()), base, candidate)
        pushed = await self._operate(
            row,
            base,
            action,
            value or {"destination_ref": base["target_ref"]},
            self._writer(row, base, binding),
            protocol=protocol,
            target_ref=target_ref,
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

    async def snapshot(self, row, target_ref=None):
        """Save the working files to ``target_ref`` or the configured backup branch."""
        grant = await self._grant(row)
        use_project_default = (
            target_ref is None
            and not row.get("agent_registration_id")
            and bool(row.get("is_default"))
            and get_repository_policy().backup.target == "project"
        )
        if use_project_default:
            base = await self._describe(row, grant)
        else:
            base = await self._describe(row, grant, target_ref or row["target_ref"])
        destination_ref = base["target_ref"]
        is_project_backup = use_project_default or (
            target_ref is not None and destination_ref != row["target_ref"]
        )
        tip = base["expected_oid"]
        if is_project_backup:
            # The backup's tree is the Workspace's files. Landing it on a main
            # that moved without this Workspace would roll that work back, so
            # it lands only on a commit the Workspace knows: the one it booted
            # from, its last backup, or one it took in through sync.
            saved_oid = row.get("saved_oid")
            init_base_oid = ((row.get("resource") or {}).get("initialization") or {}).get(
                "base_oid"
            )
            synced_oid = row.get("synced_oid")
            known = {oid for oid in (saved_oid, init_base_oid, synced_oid) if oid is not None}
            if (tip is not None and tip not in known) or (tip is None and bool(known)):
                logger.error(
                    "workspace_backup_diverged",
                    extra={
                        "workbench_id": row["id"],
                        "project_id": row["project_id"],
                        "target_ref": destination_ref,
                        "remote_tip": tip,
                        "saved_oid": saved_oid,
                        "base_oid": init_base_oid,
                        "synced_oid": synced_oid,
                    },
                )
                return {"status": "diverged", "tip": None}
        snapshot_value = {
            "message": BACKUP_MESSAGE if is_project_backup else SAVE_MESSAGE,
            "tip": tip,
        }
        if is_project_backup:
            snapshot_value["include_changed_count"] = True
        made = await self._operate(
            row,
            base,
            "snapshot",
            snapshot_value,
            self._reader(row, base),
            protocol=SNAPSHOT_PROTOCOL,
            target_ref=destination_ref,
        )
        if made["status"] == "merging":
            return {"status": "merging", "tip": None}
        candidate = made["candidate"]
        if candidate is None or candidate == tip:
            return {"status": "saved", "tip": candidate}
        status = await self._push(
            row,
            grant,
            base,
            candidate,
            action="push-snapshot",
            value={"candidate": candidate},
            protocol=SNAPSHOT_PROTOCOL,
            target_ref=destination_ref,
        )
        return {"status": "saved" if status == "pushed" else status, "tip": candidate}

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
        result = await self._operate(row, base, "sync", value, self._reader(row, base))
        # The Project's branch is in the Workspace now: its backups may land on it again.
        if (
            source == project["target_ref"]
            and project["expected_oid"]
            and result.get("status") in SYNCED
        ):
            await run_blocking(self.service.repository.record_sync, row, project["expected_oid"])
        return result

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
