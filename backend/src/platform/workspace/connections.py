"""Durable external-shell occupancy and cleanup, independent of access protocol."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from src.platform.authorization.models import ProjectAction

from .errors import WorkspaceError
from .execution.shell import WorkspaceShell
from .repository import WorkbenchRepository

logger = logging.getLogger(__name__)


class ConnectionRepository(WorkbenchRepository):
    def reserve(self, identity, row, revision):
        return self.command(
            "connection_acquire",
            id=identity,
            workbench=row["id"],
            generation=row["generation"],
            user=row["user_id"],
            project=row["project_id"],
            revision=revision,
        )

    def update(self, connection, action):
        return self.command(
            "connection_update",
            id=connection["id"],
            generation=connection["generation"],
            action=action,
        )

    def pending(self):
        return (
            self.client.table("cloud_workbench_connections")
            .select("*")
            .neq("state", "released")
            .or_(f"state.eq.stopping,lease_until.lt.{datetime.now(UTC).isoformat()}")
            .order("lease_until")
            .limit(100)
            .execute()
            .data
        )


class WorkspaceConnections:
    def __init__(self, workspace, repository=None, shell_factory=WorkspaceShell):
        self.workspace = workspace
        self.repository = repository or ConnectionRepository()
        self.shell_factory = shell_factory

    async def authorize(self, user, project, workbench, generation):
        context, _ = await asyncio.to_thread(
            self.workspace.context, user, project, ProjectAction.AGENT_RUN
        )
        row = context["workbench"]
        if (
            not row
            or row["id"] != workbench
            or row["generation"] != generation
            or row["state"] != "running"
            or row["owner_deleted_at"] is not None
        ):
            raise WorkspaceError(409, {"code": "workspace_connection_unavailable"})
        await self.workspace.check(row)
        return row, context["revision"]

    @asynccontextmanager
    async def open(self, identity, user, project, workbench, generation):
        identity = str(UUID(identity))
        row, revision = await self.authorize(user, project, workbench, generation)
        handle = await self.workspace.connection(row, capabilities={"workspace_shell"})
        connection = await asyncio.to_thread(self.repository.reserve, identity, row, revision)
        shell = self.shell_factory(connection)
        try:
            await shell.connect(handle)
            yield connection, shell
        finally:
            # Unconfirmed stop remains durable and blocks pause. The reaper can
            # reconnect using this exact identity even after access is revoked.
            try:
                await self.cleanup(connection, shell)
            finally:
                await shell.close()

    async def renew(self, connection, shell):
        await self.authorize(
            connection["user_id"],
            connection["project_id"],
            connection["workbench_id"],
            connection["generation"],
        )
        await asyncio.to_thread(self.repository.update, connection, "renew")
        await shell.control("heartbeat")

    async def cleanup(self, connection, shell=None, *, expired=False):
        current = await asyncio.to_thread(
            self.repository.update, connection, "expire" if expired else "stop"
        )
        if current["state"] == "released" or (expired and current["state"] == "active"):
            return
        owned = shell is None
        shell = shell or self.shell_factory(connection)
        try:
            async with asyncio.timeout(30):
                await shell.stop()
                await asyncio.to_thread(self.repository.update, connection, "release")
        finally:
            if owned:
                await shell.close()

    async def reap(self):
        for connection in await asyncio.to_thread(self.repository.pending):
            try:
                await self.cleanup(connection, expired=True)
            except Exception:
                logger.exception(
                    "workspace_connection_cleanup_pending", extra={"connection": connection["id"]}
                )
