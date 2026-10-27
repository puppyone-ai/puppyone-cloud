"""Bounded thread fan-out for Version Engine storage (no event loop).

One process-wide pool (``VERSION_STORAGE_IO_THREADS``) runs the parallel object
I/O inside a single storage call. It never borrows request threads, and no
thread ever waits for a task that has not started, so nesting cannot deadlock:

- ``parallel_each``: the calling thread works through the items itself while
  pool helpers join in; helpers that have not started when the items run out
  are cancelled, and only running helpers are awaited.
- ``submit``: for a coordinator that must keep its own thread free (stream
  heartbeats). Inside a pool thread it runs the call inline instead of queueing.

Every task runs in a copy of the submitter's context (``Execution``,
publication/collection context, I/O credits, traces). Started work is always
joined: a failure stops new items, waits for running ones, then raises the
first error. Cancellation is cooperative through ``checkpoint()``.
"""

from __future__ import annotations

import contextvars
import threading
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()
_local = threading.local()


def _mark_pool_thread() -> None:
    _local.storage_pool = True


def in_storage_pool() -> bool:
    return getattr(_local, "storage_pool", False)


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                from src.config import settings

                _executor = ThreadPoolExecutor(
                    max_workers=settings.VERSION_STORAGE_IO_THREADS,
                    thread_name_prefix="version-storage-io",
                    initializer=_mark_pool_thread,
                )
    return _executor


def submit[T](function: Callable[..., T], /, *args: Any) -> Future[T]:
    """Run ``function`` on the pool in a copy of the caller's context."""
    context = contextvars.copy_context()
    if in_storage_pool():
        future: Future[T] = Future()
        try:
            future.set_result(context.run(function, *args))
        except BaseException as exc:  # mirror ThreadPoolExecutor: the future owns it
            future.set_exception(exc)
        return future
    return _pool().submit(context.run, function, *args)


def join(futures: Iterable[Future]) -> list[Any]:
    """Wait for every future; return each result or exception, never cancelling."""
    futures = list(futures)
    wait(futures)
    outcomes = []
    for future in futures:
        error = future.exception()
        outcomes.append(error if error is not None else future.result())
    return outcomes


def parallel_each[T, R](
    function: Callable[[T], R], items: Iterable[T], *, concurrency: int
) -> list[R]:
    """Apply ``function`` to every item with at most ``concurrency`` threads.

    The caller is one of those threads. Results keep the input order.
    """
    items = list(items)
    if not items:
        return []
    if type(concurrency) is not int or concurrency <= 0:
        raise ValueError("concurrency must be a positive integer")
    results: list[Any] = [None] * len(items)
    queue = deque(range(len(items)))
    lock = threading.Lock()
    errors: list[BaseException] = []

    def drain() -> None:
        while True:
            with lock:
                if errors or not queue:
                    return
                index = queue.popleft()
            try:
                results[index] = function(items[index])
            except BaseException as exc:
                with lock:
                    errors.append(exc)
                return

    helpers = [
        _pool().submit(contextvars.copy_context().run, drain)
        for _ in range(min(concurrency, len(items)) - 1)
    ]
    try:
        drain()
    finally:
        for helper in helpers:
            helper.cancel()  # not started: nothing left to take
        wait([helper for helper in helpers if not helper.cancelled()])
    if errors:
        raise errors[0]
    return results
