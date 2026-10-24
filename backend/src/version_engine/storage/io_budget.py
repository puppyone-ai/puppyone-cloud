"""Physical S3 I/O credits shared by every Version Engine storage thread.

The process shares one window of concurrent physical transfers. A streamed
operation additionally shares one smaller window across *all* of its batches,
so nested bundle/chunk fan-out cannot multiply the configured concurrency.
Waiting for a credit stays cooperative: a cancelled ``Execution`` stops it.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar

from src.infra.execution.context import checkpoint
from src.version_engine.write_engine.trace import trace_phase

_PROCESS_LIMIT = 8
_process_credits = threading.BoundedSemaphore(_PROCESS_LIMIT)
_operation_credits: ContextVar[threading.BoundedSemaphore | None] = ContextVar(
    "version_storage_io_credits",
    default=None,
)


@contextmanager
def operation_io_budget(concurrency: int):
    current = _operation_credits.get()
    token = _operation_credits.set(current or threading.BoundedSemaphore(concurrency))
    try:
        yield
    finally:
        _operation_credits.reset(token)


def _acquire(credits: threading.BoundedSemaphore) -> None:
    while not credits.acquire(timeout=0.5):
        checkpoint()


@contextmanager
def physical_io_credit():
    operation_credit = _operation_credits.get()
    with trace_phase("s3.io.queue"):
        if operation_credit is not None:
            _acquire(operation_credit)
        try:
            _acquire(_process_credits)
        except BaseException:
            if operation_credit is not None:
                operation_credit.release()
            raise
    try:
        yield
    finally:
        _process_credits.release()
        if operation_credit is not None:
            operation_credit.release()
