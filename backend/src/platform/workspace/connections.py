"""Durable external-shell occupancy and cleanup, independent of access protocol."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from postgrest.exceptions import APIError

from src.infra.execution.runner import run_blocking
from src.platform.authorization.models import ProjectAction

from .errors import WorkspaceError
from .execution.shell import WorkspaceShell
from .repository import WorkbenchRepository

logger = logging.getLogger(__name__)

# States that resume on the same generation, so a bound credential stays valid.
RESUMABLE_STATES = frozenset({"paused", "pausing", "resuming"})


class ConnectionRepository(WorkbenchRepository):
    def reserve(self, identity, row, revision):
        try:
            return self.command(
                "connection_acquire",
                id=identity,
                workbench=row["id"],
                generation=row["generation"],
                user=row["user_id"],
                project=row["project_id"],
                revision=revision,
            )
        except APIError as exc:
            # Bounded occupancy is an expected, actionable refusal.
            if "workspace_connection_limit" in str(exc.message or exc):
                raise WorkspaceError(429, {"code": "workspace_connection_limit"}) from exc
            raise

    def update(self, connection, action):
        return self.command(
            "connection_update",
            id=connection["id"],
            generation=connection["generation"],
            action=action,
        )

    def pending(self, pool=None):
        """Unreleased connections whose lease lapsed or whose stop is unconfirmed.

        ``pool`` limits reclamation to workspaces maintained by that worker
        pool, so a development pool sharing the database never stops another
        environment's sessions.
        """
        query = self.client.table("cloud_workbench_connections")
        if pool is None:
            query = query.select("*")
        else:
            query = query.select("*, cloud_workbenches!inner(worker_pool)").eq(
                "cloud_workbenches.worker_pool", pool
            )
        rows = (
            query.neq("state", "released")
            .or_(f"state.eq.stopping,lease_until.lt.{datetime.now(UTC).isoformat()}")
            .order("lease_until")
            .limit(100)
            .execute()
            .data
        )
        for row in rows:
            row.pop("cloud_workbenches", None)
        return rows


class WorkspaceConnections:
    def __init__(self, workspace, repository=None, shell_factory=WorkspaceShell):
        self.workspace = workspace
        self.repository = repository or ConnectionRepository()
        self.shell_factory = shell_factory

    async def authorize(self, user, project, workbench, generation, *, running=True):
        """Owner authority and exact identity; ``running`` also requires live compute.

        ``running=False`` admits a paused workspace of the same generation so a
        caller may resume it; the connection itself is always re-authorized
        against running compute.
        """
        context, _ = await run_blocking(
            self.workspace.context, user, project, ProjectAction.AGENT_RUN
        )
        row = context["workbench"]
        states = {"running"} if running else {"running", *RESUMABLE_STATES}
        if (
            not row
            or row["id"] != workbench
            or row["generation"] != generation
            or row["state"] not in states
            or row["owner_deleted_at"] is not None
        ):
            raise WorkspaceError(409, {"code": "workspace_connection_unavailable"})
        if running:
            await self.workspace.check(row)
        return row, context["revision"]

    @asynccontextmanager
    async def open(self, identity, user, project, workbench, generation):
        identity = str(UUID(identity))
        row, revision = await self.authorize(user, project, workbench, generation)
        handle = await self.workspace.connection(row, capabilities={"workspace_shell"})
        connection = await run_blocking(self.repository.reserve, identity, row, revision)
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
        await run_blocking(self.repository.update, connection, "renew")
        await shell.control("heartbeat")

    async def cleanup(self, connection, shell=None, *, expired=False):
        current = await run_blocking(
            self.repository.update, connection, "expire" if expired else "stop"
        )
        if current["state"] == "released" or (expired and current["state"] == "active"):
            return
        owned = shell is None
        shell = shell or self.shell_factory(connection)
        try:
            async with asyncio.timeout(30):
                await shell.stop()
                await run_blocking(self.repository.update, connection, "release")
        finally:
            if owned:
                await shell.close()

    async def in_pool(self, connection, pool):
        """Whether ``connection`` belongs to a workspace maintained by ``pool``."""
        if pool is None:
            return True
        row = await run_blocking(self.repository.get, connection["workbench_id"])
        return bool(row) and row.get("worker_pool") == pool

    async def reap(self, pool=None):
        for connection in await run_blocking(self.repository.pending, pool):
            try:
                await self.cleanup(connection, expired=True)
            except Exception:
                logger.exception(
                    "workspace_connection_cleanup_pending", extra={"connection": connection["id"]}
                )
