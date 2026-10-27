"""Per-process concurrency set-up for API and worker entry points."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from loguru import logger

from src.infra.execution.runner import configure_blocking_capacity

_LAG_SAMPLE_SECONDS = 0.5


async def watch_event_loop_lag(threshold_seconds: float) -> None:
    """Log every stall of the running loop that exceeds ``threshold_seconds``.

    A sleeping task wakes late exactly by the time some callback held the loop,
    e.g. a synchronous database or S3 call made inline from a coroutine.
    """
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        await asyncio.sleep(_LAG_SAMPLE_SECONDS)
        lag = loop.time() - started - _LAG_SAMPLE_SECONDS
        if lag >= threshold_seconds:
            logger.bind(component="event_loop", lag_ms=round(lag * 1000, 1)).warning(
                "event_loop_lag"
            )


class ProcessConcurrency:
    """Apply the shared blocking capacity and own the loop-lag monitor."""

    def __init__(self) -> None:
        self._monitor: asyncio.Task | None = None

    def start(self, *, blocking_threads: int, lag_warn_ms: int) -> None:
        configure_blocking_capacity(blocking_threads)
        if lag_warn_ms > 0 and self._monitor is None:
            self._monitor = asyncio.create_task(
                watch_event_loop_lag(lag_warn_ms / 1000), name="event-loop-lag-monitor"
            )

    async def stop(self) -> None:
        monitor, self._monitor = self._monitor, None
        if monitor is not None:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor


def start_process_concurrency() -> ProcessConcurrency:
    """Configure this process from settings; call inside the running loop."""
    from src.config import settings

    concurrency = ProcessConcurrency()
    concurrency.start(
        blocking_threads=settings.BLOCKING_THREAD_LIMIT,
        lag_warn_ms=settings.EVENT_LOOP_LAG_WARN_MS,
    )
    return concurrency
