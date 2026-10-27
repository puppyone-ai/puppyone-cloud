"""Access revocation coordinates with Workspace; it never owns process cleanup."""

import logging

from src.infra.execution.runner import run_blocking

from .grant_repository import AccessRepository


class AccessSessions:
    def __init__(self, repository=None):
        self.repository = repository or AccessRepository()

    async def finish_unallocated(self, identity):
        await run_blocking(
            self.repository.call, "access_session_command", action="finish_unallocated", id=identity
        )

    async def ready(self):
        await run_blocking(self.repository.call, "access_session_command", action="ready")

    async def reap(self, connections, pool=None):
        pending = await run_blocking(
            self.repository.call, "access_session_command", action="pending"
        )
        for connection in pending:
            if pool is not None and not await connections.in_pool(connection, pool):
                continue
            # The Workspace owner releases its durable occupancy only after the
            # actual stop acknowledgement. SQL mirrors that outcome to Access.
            try:
                await connections.cleanup(connection)
            except Exception:
                # Other connections must still be drained when one provider is
                # unavailable. Occupancy stays with Workspace until stop is confirmed.
                logging.getLogger(__name__).warning("access_session_cleanup_pending")


async def reclaim_external_connections(workbenches, pool):
    """Sandbox-module pass: stop lapsed and revoked external sessions of ``pool``.

    Gateways end their own sessions; this reclaims what a lost gateway left
    behind. Workspace releases occupancy only after the stop is acknowledged.
    """
    from src.platform.workspace.connections import WorkspaceConnections

    connections = WorkspaceConnections(workbenches)
    await connections.reap(pool)
    await AccessSessions().reap(connections, pool)
