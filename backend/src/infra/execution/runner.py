"""The single boundary between async edges and the synchronous core.

Async code calls synchronous work only through these helpers:

- ``run_blocking``: one blocking call in the process's shared worker threads.
- ``run_owned``: ``run_blocking`` for repository operations, with a
  deadline-bound ``Execution``, optional disconnect polling and closing of a
  result that arrives after the caller left.
- ``run_cleanup``: mandatory release work that no ``Execution`` may stop.
- ``await_owned``: the same ownership rule for an awaitable.
- ``iterate_blocking``: consume a synchronous iterator (e.g. an S3 body stream)
  from async code, one ``next`` per worker call, closed when the consumer leaves.

Threads cannot be killed. A cancelled caller therefore always waits for its
started work to return (an active ``Execution`` is cancelled first, so
``checkpoint()`` lets the work stop early), then the cancellation propagates.
Leases, pins and request files owned by the caller outlive the physical I/O.

Capacity is one per-process limit shared with FastAPI ``def`` routes, sync
dependencies and sync streaming iterators: AnyIO's default thread limiter,
sized by ``configure_blocking_capacity`` at process start-up.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import suppress
from typing import Any

import anyio.to_thread
from loguru import logger

from src.infra.execution.context import Execution, current_execution


def configure_blocking_capacity(total: int) -> None:
    """Size the shared worker-thread pool; call inside the running event loop."""
    if type(total) is not int or total <= 0:
        raise ValueError("blocking thread capacity must be a positive integer")
    anyio.to_thread.current_default_thread_limiter().total_tokens = total


async def await_owned[T](
    awaitable: Awaitable[T],
    *,
    on_cancel: Callable[[], Any] | None = None,
    release: Callable[[T], Awaitable[Any]] | None = None,
) -> T:
    """Await ``awaitable`` to completion even if the caller is cancelled.

    On cancellation ``on_cancel`` runs immediately, the work is drained despite
    repeated cancellation, a successful late result is passed to ``release``
    and the caller's cancellation propagates.

    The cancellation always remains the caller's outcome. Raising a late
    failure instead would let an ``except Exception`` handler swallow the
    cancellation and keep running; the failure is logged unless it is the
    expected reaction to ``on_cancel``.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_cancel is not None:
            on_cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            error = task.exception()
            if error is None and release is not None:
                await release(task.result())
            elif error is not None and on_cancel is None:
                logger.bind(component="execution", error_type=type(error).__name__).warning(
                    "owned_work_failed_after_cancellation"
                )
        raise


def _blocking[T](function: Callable[..., T], args, kwargs) -> Awaitable[T]:
    call = functools.partial(function, *args, **kwargs) if kwargs else function
    return anyio.to_thread.run_sync(call, *(() if kwargs else args))


async def run_blocking[T](function: Callable[..., T], /, *args, **kwargs) -> T:
    """Run synchronous ``function`` in the shared pool and own its lifetime.

    Context variables are copied into the worker. No ``Execution`` or deadline
    is created; an existing one is signalled if the caller is cancelled.
    """
    execution = current_execution.get()
    return await await_owned(
        _blocking(function, args, kwargs),
        on_cancel=None if execution is None else execution.cancelled.set,
    )


async def _close_late_result(response) -> None:
    close = getattr(response, "close", None)
    if close is not None:
        await run_cleanup(close)


async def run_owned(function, *args, cancel_when=None, **kwargs):
    """Run a repository operation under a cooperative, deadline-bound Execution.

    Killing a Git process is safe; abandoning a thread with an outstanding S3
    PUT is not. The storage layer retains unknown I/O in its durable ledger.
    ``cancel_when`` is polled (e.g. client disconnect) to stop cooperatively.
    """
    execution = current_execution.get() or Execution()
    token = current_execution.set(execution)

    def execute():
        execution.check()
        return function(*args, **kwargs)

    try:
        work = asyncio.ensure_future(_blocking(execute, (), {}))
    finally:
        current_execution.reset(token)

    async def monitor():
        while not work.done():
            try:
                cancelled = await cancel_when()
            except Exception:
                cancelled = True
            if cancelled:
                execution.cancelled.set()
                return
            await asyncio.sleep(0.1)

    watcher = asyncio.create_task(monitor()) if cancel_when is not None else None
    try:
        return await await_owned(
            work, on_cancel=execution.cancelled.set, release=_close_late_result
        )
    finally:
        if watcher is not None:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher


async def run_cleanup(function, *args):
    """Cleanup is mandatory even when the operation deadline already expired.

    Cancellation is propagated after cleanup joins, never into the worker.
    Do not use this for business work or for starting new external operations.
    """

    def cleanup():
        token = current_execution.set(None)
        try:
            return function(*args)
        finally:
            current_execution.reset(token)

    return await await_owned(_blocking(cleanup, (), {}))


_END = object()


async def iterate_blocking[T](iterable: Iterable[T]) -> AsyncIterator[T]:
    """Yield a synchronous iterator's items without blocking the event loop.

    Each ``next`` runs through ``run_blocking``. The iterator is closed with
    ``run_cleanup`` when iteration ends, fails or the consumer closes this
    generator (use ``contextlib.aclosing`` when not fully consuming it).
    """
    iterator = iter(iterable)
    try:
        while True:
            item = await run_blocking(next, iterator, _END)
            if item is _END:
                return
            yield item
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            await run_cleanup(close)
