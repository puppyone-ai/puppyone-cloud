"""The single async -> blocking boundary: one pool, owned threads."""

from __future__ import annotations

import asyncio
import threading
import time
from contextvars import ContextVar

import anyio.to_thread
import pytest

from src.infra.execution.context import Execution, current_execution
from src.infra.execution.runner import (
    await_owned,
    configure_blocking_capacity,
    run_blocking,
)

_marker: ContextVar[str] = ContextVar("blocking_boundary_marker", default="")


async def _until(predicate, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.002)


async def test_runs_off_the_loop_with_arguments_and_copied_context():
    loop_thread = threading.get_ident()
    token = _marker.set("request-7")
    try:
        result = await run_blocking(
            lambda a, *, b: (threading.get_ident(), _marker.get(), a + b), 2, b=3
        )
    finally:
        _marker.reset(token)
    worker_thread, marker, total = result
    assert worker_thread != loop_thread
    assert (marker, total) == ("request-7", 5)


async def test_does_not_invent_an_execution_or_deadline():
    assert await run_blocking(current_execution.get) is None


async def test_cancellation_joins_the_thread_and_signals_the_active_execution():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    execution = Execution()

    def work():
        started.set()
        assert release.wait(3)
        finished.set()
        return "late"

    token = current_execution.set(execution)
    try:
        task = asyncio.create_task(run_blocking(work))
    finally:
        current_execution.reset(token)
    await _until(started.is_set)
    task.cancel()
    await asyncio.sleep(0.02)
    task.cancel()  # repeated cancellation cannot detach the worker either
    await asyncio.sleep(0.02)
    assert not task.done()
    assert execution.cancelled.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_shares_the_anyio_default_limiter_with_sync_routes():
    limiter = anyio.to_thread.current_default_thread_limiter()
    previous = limiter.total_tokens
    active = peak = 0
    lock = threading.Lock()

    def work():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1

    try:
        configure_blocking_capacity(2)
        assert limiter.total_tokens == 2
        await asyncio.gather(*(run_blocking(work) for _ in range(6)))
    finally:
        limiter.total_tokens = previous
    assert peak == 2


def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        configure_blocking_capacity(0)


async def test_await_owned_releases_a_result_that_arrives_after_cancellation():
    gate = asyncio.Event()
    released = []

    async def produce():
        await gate.wait()
        return "resource"

    async def release(value):
        released.append(value)

    task = asyncio.create_task(await_owned(produce(), release=release))
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released == ["resource"]


async def test_await_owned_returns_and_raises_normally_without_cancellation():
    async def ok():
        return 1

    async def fail():
        raise LookupError("x")

    assert await await_owned(ok()) == 1
    with pytest.raises(LookupError):
        await await_owned(fail())


async def test_iterate_blocking_pulls_off_the_loop_and_closes_early_exit():
    from contextlib import aclosing

    from src.infra.execution.runner import iterate_blocking

    loop_thread = threading.get_ident()
    threads, closed = [], []

    def chunks():
        try:
            for index in range(5):
                threads.append(threading.get_ident())
                yield index
        finally:
            closed.append(threading.get_ident())

    seen = []
    async with aclosing(iterate_blocking(chunks())) as stream:
        async for item in stream:
            seen.append(item)
            if item == 2:
                break
    assert seen == [0, 1, 2]
    assert loop_thread not in threads
    assert closed and closed[0] != loop_thread

    assert [item async for item in iterate_blocking([7, 8])] == [7, 8]
