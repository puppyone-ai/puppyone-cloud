"""Async adapters for synchronous Git contexts that acquire file locks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from src.infra.execution.runner import await_owned, run_blocking, run_cleanup


async def enter_sync_context_off_loop(cm) -> Any:
    """Enter ``cm`` in a worker without leaking it when the caller cancels."""

    async def release(_value) -> None:
        # The thread acquired the context after the caller left: release it.
        await run_cleanup(cm.__exit__, None, None, None)

    return await await_owned(run_blocking(cm.__enter__), release=release)


async def _exit_sync_context_off_loop(cm, exc_info: tuple) -> bool:
    # Context release is mandatory even if disconnect/shutdown cancels the
    # request while __exit__ is waiting for its worker turn.
    return bool(await run_cleanup(cm.__exit__, *exc_info))


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
