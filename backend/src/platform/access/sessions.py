"""Access revocation coordinates with Workspace; it never owns process cleanup."""

import asyncio
import logging

from .grant_repository import AccessRepository


class AccessSessions:
    def __init__(self, repository=None):
        self.repository = repository or AccessRepository()

    async def finish_unallocated(self, identity):
        await asyncio.to_thread(
            self.repository.call, "access_session_command", action="finish_unallocated", id=identity
        )

    async def ready(self):
        await asyncio.to_thread(self.repository.call, "access_session_command", action="ready")

    async def reap(self, connections):
        pending = await asyncio.to_thread(
            self.repository.call, "access_session_command", action="pending"
        )
        for connection in pending:
            # The Workspace owner releases its durable occupancy only after the
            # actual stop acknowledgement. SQL mirrors that outcome to Access.
            try:
                await connections.cleanup(connection)
            except Exception:
                # Other connections must still be drained when one provider is
                # unavailable. Occupancy stays with Workspace until stop is confirmed.
                logging.getLogger(__name__).warning("access_session_cleanup_pending")
