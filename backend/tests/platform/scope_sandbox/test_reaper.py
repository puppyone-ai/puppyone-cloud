"""Tests for the periodic reaper loop."""

from __future__ import annotations

import asyncio

from src.platform.workspace.scopes.reaper import reaper_loop, run_reaper_once


class _FakeManager:
    def __init__(self, raise_times: int = 0) -> None:
        self.calls = 0
        self._raise_times = raise_times

    async def reap(self, *, now=None):
        self.calls += 1
        if self.calls <= self._raise_times:
            raise RuntimeError("reap boom")
        return {"pass": self.calls}


async def test_run_reaper_once_delegates():
    mgr = _FakeManager()
    out = await run_reaper_once(mgr)
    assert mgr.calls == 1 and out == {"pass": 1}


async def test_reaper_loop_runs_until_stopped():
    mgr = _FakeManager()
    stop = asyncio.Event()

    def on_result(_):
        if mgr.calls >= 3:
            stop.set()

    await asyncio.wait_for(
        reaper_loop(mgr, interval_s=0, stop_event=stop, on_result=on_result), timeout=5
    )
    assert mgr.calls >= 3


async def test_reaper_loop_survives_failing_pass():
    mgr = _FakeManager(raise_times=2)   # first two passes raise
    stop = asyncio.Event()

    def on_result(_):
        if mgr.calls >= 4:
            stop.set()

    await asyncio.wait_for(
        reaper_loop(mgr, interval_s=0, stop_event=stop, on_result=on_result), timeout=5
    )
    assert mgr.calls >= 4   # kept going past the two failures


async def test_reaper_loop_exits_if_stopped_before_start():
    mgr = _FakeManager()
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(reaper_loop(mgr, interval_s=0, stop_event=stop), timeout=5)
    assert mgr.calls == 0
