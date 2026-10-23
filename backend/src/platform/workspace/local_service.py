"""Local workspace use cases shared by access adapters."""

import os
import time
from dataclasses import dataclass

from src.platform.project.write_lease import ProjectWriteLease
from src.utils.logger import log_error, log_info

from .errors import WorkspaceError
from .sync_worker import SyncWorker


@dataclass(frozen=True)
class WorkspaceCompletion:
    agent_id: str
    total_files: int
    committed: int
    conflict_count: int
    strategies: list[str]


class LocalWorkspaceService:
    def __init__(self, provider=None, *, lease_factory=ProjectWriteLease, sync_factory=SyncWorker):
        from .provider import get_workspace_provider

        self.provider = provider or get_workspace_provider()
        self.lease_factory, self.sync_factory = lease_factory, sync_factory

    async def create(self, project_id, agent_id, ops, grant):
        agent_id = agent_id or f"ext-{int(time.time() * 1000)}"
        sync = self.sync_factory(ops=ops.for_grant(grant), base_dir=self.provider._base_dir)
        async with self.lease_factory(project_id, "workspace.create"):
            result = await sync.sync_project(project_id)
            return await self.provider.create_workspace(
                agent_id=agent_id,
                project_id=project_id,
                base_commit_id=result.get("head_commit_id") or None,
            )

    def info(self, agent_id):
        return self.provider.get_workspace_info(agent_id)

    @staticmethod
    def exists(info):
        return info is not None and os.path.exists(info.path)

    async def complete(self, project_id, agent_id, commands):
        info = self.info(agent_id)
        if info is not None and info.project_id != project_id:
            raise WorkspaceError(403, "Workspace does not belong to this project")
        changes = await self.provider.detect_changes(agent_id)
        modified = {
            path: value if isinstance(value, bytes) else str(value).encode("utf-8")
            for path, value in changes.modified.items()
        }
        deleted = list(changes.deleted)
        total = len(modified) + len(deleted)
        try:
            if not total:
                return WorkspaceCompletion(agent_id, 0, 0, 0, [])
            outcome = await commands.bulk_write(
                project_id,
                modified,
                actor=agent_id,
                deleted=deleted,
                message=f"Agent workspace merge ({len(modified)} modified, {len(deleted)} deleted)",
            )
            result = outcome.result
            log_info(
                f"[Workspace] version push: commit={result.commit_id or '(none)'} files={total}"
            )
            return WorkspaceCompletion(
                agent_id,
                total,
                len(modified),
                result.conflicts,
                ["merge"] if result.merged else [],
            )
        except Exception as exc:
            log_error(f"[Workspace] version push failed: {exc}")
            raise WorkspaceError(500, "Workspace merge failed") from exc
        finally:
            await self.provider.cleanup(agent_id)
