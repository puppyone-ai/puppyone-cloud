"""Bounded request coalescing, never durable ownership or an authorization cache."""

import asyncio
from dataclasses import dataclass

from .errors import WorkspaceError


@dataclass
class _Observation:
    task: asyncio.Task
    users: int = 0


class OpeningObservers:
    def __init__(self, *, limit=128):
        self.limit = limit
        self.entries = {}
        self.closed = False

    async def observe(self, key, create):
        if self.closed:
            raise WorkspaceError(503, {"code": "workbench_observer_unavailable"})
        entry = self.entries.get(key)
        if entry is None or entry.task.done():
            if entry is None and len(self.entries) >= self.limit:
                raise WorkspaceError(
                    503, {"code": "workbench_observer_capacity", "retryable": True}
                )
            entry = _Observation(asyncio.create_task(create()))
            self.entries[key] = entry
        entry.users += 1
        try:
            # One disconnected request cannot cancel another consumer's wait.
            return await asyncio.shield(entry.task)
        finally:
            entry.users -= 1
            if not entry.users:
                if self.entries.get(key) is entry:
                    self.entries.pop(key)
                entry.task.cancel()
                await asyncio.gather(entry.task, return_exceptions=True)

    async def close(self):
        self.closed = True
        entries, self.entries = self.entries, {}
        for entry in entries.values():
            entry.task.cancel()
        await asyncio.gather(*(entry.task for entry in entries.values()), return_exceptions=True)
