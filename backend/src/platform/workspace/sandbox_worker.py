"""Sandbox module of the worker process: the only owner of machine lifecycle.

It executes this pool's workspace open requests, maintains running workspaces
(idle pause and health) and reclaims unretained ephemeral executions and
machines left by the retired Scope Sandbox. API, the SSH gateway and the agent
module only record intents in PostgreSQL; nothing here supervises Runs.

Every pass is idempotent and claims through SQL, so any number of replicas may
run this module. A failing pass is logged and retried on the next interval.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress

from src.config import settings

logger = logging.getLogger(__name__)

MAINTENANCE_INTERVAL_SECONDS = 30
STATS_INTERVAL_SECONDS = 60

Pass = tuple[str, Callable[[], Awaitable[object]]]


async def _until(stop: asyncio.Event, seconds: float) -> None:
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


class SandboxModule:
    """Run the lifecycle loop and periodic reclamation until ``stop`` is set.

    ``passes`` run every maintenance interval in order; ``slow_passes`` run
    every ``SANDBOX_REAPER_INTERVAL_S`` when ``SANDBOX_REAPER_ENABLED``.
    """

    def __init__(
        self,
        workbenches,
        *,
        pool: str,
        passes: Sequence[Pass] = (),
        slow_passes: Sequence[Pass] | None = None,
        maintenance_interval: float = MAINTENANCE_INTERVAL_SECONDS,
        stats_interval: float = STATS_INTERVAL_SECONDS,
    ):
        self.workbenches = workbenches
        self.pool = pool
        maintenance = [("workbench_maintenance", workbenches.reap)] if workbenches else []
        self.passes = [*maintenance, *passes]
        self.slow_passes = default_slow_passes() if slow_passes is None else list(slow_passes)
        self.maintenance_interval = maintenance_interval
        self.stats_interval = stats_interval
        self.completed: Counter[str] = Counter()
        self.failed: Counter[str] = Counter()

    async def serve(self, *, stop: asyncio.Event, grace: float = 0) -> None:
        tasks = [asyncio.create_task(self._maintain(stop))]
        if self.workbenches:
            lifecycle = self.workbenches.lifecycle.serve(stop=stop, grace=grace)
            tasks.append(asyncio.create_task(lifecycle))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, name: str, step: Callable[[], Awaitable[object]]) -> None:
        try:
            await step()
            self.completed[name] += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failed[name] += 1
            logger.exception("sandbox_pass_failed", extra={"worker_stats": {"pass": name}})

    async def _maintain(self, stop: asyncio.Event) -> None:
        slow_due = 0.0
        stats_due = time.monotonic() + self.stats_interval
        while not stop.is_set():
            for name, step in self.passes:
                await self._run(name, step)
            if settings.SANDBOX_REAPER_ENABLED and time.monotonic() >= slow_due:
                for name, step in self.slow_passes:
                    await self._run(name, step)
                slow_due = time.monotonic() + max(1, settings.SANDBOX_REAPER_INTERVAL_S)
            if time.monotonic() >= stats_due:
                self.report()
                stats_due = time.monotonic() + self.stats_interval
            await _until(stop, self.maintenance_interval)

    def report(self) -> dict:
        """Log and return bounded counters since the previous report."""
        lifecycle = self.workbenches.lifecycle if self.workbenches else None
        pending, oldest = lifecycle.pending_snapshot if lifecycle else (0, 0.0)
        stats = {
            "module": "sandbox",
            "pool": self.pool,
            "open_operations_active": len(lifecycle.active) if lifecycle else 0,
            "open_requests_pending": pending,
            "oldest_open_request_seconds": oldest,
            "passes_completed": dict(self.completed),
            "passes_failed": dict(self.failed),
        }
        self.completed.clear()
        self.failed.clear()
        logger.info("sandbox_worker_stats", extra={"worker_stats": stats})
        return stats


def default_slow_passes() -> list[Pass]:
    """Provider-side reclamation formerly run by the API process."""
    from src.platform.sandbox import reclamation_passes

    return reclamation_passes()
