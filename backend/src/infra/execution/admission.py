"""Bounded admission for interactive blocking reads, local to one ASGI loop."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class ExecutionCapacityError(RuntimeError):
    """Retryable saturation; no work has started."""


class ReadAdmission:
    def __init__(self, *, running: int = 8, waiting: int = 32, wait_seconds: float = 10):
        if running < 1 or waiting < 0 or wait_seconds <= 0:
            raise ValueError("Invalid read admission budget")
        self._slots = asyncio.Semaphore(running)
        self._capacity = running + waiting
        self._admitted = 0
        self._wait_seconds = wait_seconds

    @asynccontextmanager
    async def acquire(self):
        if self._admitted >= self._capacity:
            raise ExecutionCapacityError("File reads are busy. Retry shortly.")
        self._admitted += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(self._slots.acquire(), self._wait_seconds)
                acquired = True
            except TimeoutError as exc:
                raise ExecutionCapacityError("File reads are busy. Retry shortly.") from exc
            yield
        finally:
            if acquired:
                self._slots.release()
            self._admitted -= 1


def read_admission():
    # The loop owns its admission budget. No process-global asyncio primitive
    # is accidentally shared by independent worker/test event loops.
    loop = asyncio.get_running_loop()
    budget = getattr(loop, "_puppyone_read_admission", None)
    if budget is None:
        from src.config import settings

        budget = ReadAdmission(
            running=settings.FILE_READ_CONCURRENCY,
            waiting=settings.FILE_READ_QUEUE_LIMIT,
            wait_seconds=settings.FILE_READ_QUEUE_TIMEOUT_SECONDS,
        )
        loop._puppyone_read_admission = budget
    return budget.acquire()
