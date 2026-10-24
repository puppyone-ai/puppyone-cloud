"""Process start-up applies one thread capacity and reports loop stalls."""

from __future__ import annotations

import asyncio
import time

import anyio.to_thread
from loguru import logger

from src.infra.execution import process
from src.infra.execution.process import ProcessConcurrency


async def test_start_applies_capacity_and_stop_ends_the_monitor():
    limiter = anyio.to_thread.current_default_thread_limiter()
    previous = limiter.total_tokens
    concurrency = ProcessConcurrency()
    try:
        concurrency.start(blocking_threads=7, lag_warn_ms=100)
        assert limiter.total_tokens == 7
        monitor = concurrency._monitor
        assert monitor is not None and not monitor.done()
        await concurrency.stop()
        assert monitor.done() and concurrency._monitor is None
    finally:
        limiter.total_tokens = previous


async def test_zero_threshold_disables_the_monitor():
    limiter = anyio.to_thread.current_default_thread_limiter()
    previous = limiter.total_tokens
    concurrency = ProcessConcurrency()
    try:
        concurrency.start(blocking_threads=previous, lag_warn_ms=0)
        assert concurrency._monitor is None
    finally:
        await concurrency.stop()
        limiter.total_tokens = previous


async def test_inline_blocking_call_is_reported(monkeypatch):
    monkeypatch.setattr(process, "_LAG_SAMPLE_SECONDS", 0.02)
    records = []
    sink = logger.add(lambda message: records.append(message.record), level="WARNING")
    monitor = asyncio.create_task(process.watch_event_loop_lag(0.05))
    try:
        await asyncio.sleep(0.03)
        time.sleep(0.15)  # what a synchronous database call inside a coroutine does
        await asyncio.sleep(0.05)
    finally:
        monitor.cancel()
        logger.remove(sink)
    stalls = [record for record in records if record["message"] == "event_loop_lag"]
    assert stalls and stalls[0]["extra"]["lag_ms"] >= 100
