"""Owned blocking work: join before caller-owned resources are released."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from src.infra.execution.context import Execution, current_execution


async def run_owned(function, *args, cancel_when=None, **kwargs):
    """Join a cancelled worker before its request file/lease can be released.

    Killing a Git process is safe; abandoning a thread with an outstanding S3
    PUT is not. The storage layer retains unknown I/O in its durable ledger.
    """
    execution = current_execution.get() or Execution()
    token = current_execution.set(execution)

    def execute():
        execution.check()
        return function(*args, **kwargs)

    task = asyncio.create_task(asyncio.to_thread(execute))

    async def monitor():
        while not task.done():
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
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        execution.cancelled.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            error = task.exception()
            if error is None:
                response = task.result()
                close = getattr(response, "close", None)
                if close is not None:
                    await run_cleanup(close)
        raise
    finally:
        try:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
        finally:
            current_execution.reset(token)


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

    task = asyncio.create_task(asyncio.to_thread(cleanup))
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
    result = task.result()
    if cancelled is not None:
        raise cancelled
    return result
