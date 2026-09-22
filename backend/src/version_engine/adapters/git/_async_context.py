"""Async adapters for synchronous Git contexts that acquire file locks."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


async def enter_sync_context_off_loop(cm) -> Any:
    """Enter ``cm`` in a worker without leaking it when the caller cancels."""

    enter_task = asyncio.create_task(asyncio.to_thread(cm.__enter__))
    try:
        return await asyncio.shield(enter_task)
    except asyncio.CancelledError:
        # A thread keeps running after its awaiting task is cancelled. If it
        # acquired the context, release it before propagating cancellation.
        try:
            value = await enter_task
        except BaseException:
            pass
        else:
            await asyncio.to_thread(cm.__exit__, None, None, None)
        raise


async def _exit_sync_context_off_loop(cm, exc_info: tuple) -> bool:
    exit_task = asyncio.create_task(asyncio.to_thread(cm.__exit__, *exc_info))
    try:
        return bool(await asyncio.shield(exit_task))
    except asyncio.CancelledError:
        # Context release is mandatory even if disconnect/shutdown cancels the
        # request while __exit__ is waiting for its worker turn.
        await exit_task
        raise


@asynccontextmanager
async def sync_context_off_loop(cm) -> AsyncIterator[Any]:
    """Use a blocking synchronous context manager from async request code."""

    value = await enter_sync_context_off_loop(cm)
    try:
        yield value
    except BaseException as exc:
        suppressed = await _exit_sync_context_off_loop(
            cm,
            (type(exc), exc, exc.__traceback__),
        )
        if not suppressed:
            raise
    else:
        await _exit_sync_context_off_loop(cm, (None, None, None))
