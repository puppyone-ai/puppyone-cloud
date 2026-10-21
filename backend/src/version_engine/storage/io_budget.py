"""Shared physical I/O credits for the Version Engine's persistent bridge loop.

All S3 backend instances on a loop share the process-window limit. A streamed
operation additionally shares one smaller window across *all* of its batches,
so nested bundle/chunk fanout cannot multiply the configured concurrency.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from weakref import WeakValueDictionary

from src.version_engine.write_engine.trace import trace_phase

_LOOP_LIMIT = 8
# Active requests hold the semaphore strongly. Once idle, dropping it also
# releases its loop key; a contended semaphore itself references its loop.
_loop_credits = WeakValueDictionary()
_operation_credits: ContextVar[asyncio.Semaphore | None] = ContextVar(
    "version_storage_io_credits",
    default=None,
)


@contextmanager
def operation_io_budget(concurrency: int):
    current = _operation_credits.get()
    token = _operation_credits.set(current or asyncio.Semaphore(concurrency))
    try:
        yield
    finally:
        _operation_credits.reset(token)


@asynccontextmanager
async def physical_io_credit():
    loop = asyncio.get_running_loop()
    global_credit = _loop_credits.setdefault(loop, asyncio.Semaphore(_LOOP_LIMIT))
    operation_credit = _operation_credits.get()
    with trace_phase("s3.io.queue"):
        if operation_credit is not None:
            await operation_credit.acquire()
        try:
            await global_credit.acquire()
        except BaseException:
            if operation_credit is not None:
                operation_credit.release()
            raise
    try:
        yield
    finally:
        global_credit.release()
        if operation_credit is not None:
            operation_credit.release()
