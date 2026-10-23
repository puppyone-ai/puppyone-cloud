"""Periodic reaper for scope sandboxes.

The reclamation policy already lives in ``ScopeSandboxManager.reap()``; this just
calls it on an interval (idle RUNNING → STOP, long-idle STOPPED → DESTROY).

The app lifecycle wires this loop when ``SCOPE_SANDBOX_REAPER_ENABLED`` is set.
Production uses the external Supabase store, so every worker observes the same
session records; provider operations remain idempotent under overlapping passes.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Protocol

from src.utils.logger import log_warning


class _Reapable(Protocol):
    async def reap(self, *, now: float | None = None): ...


async def run_reaper_once(manager: _Reapable, *, now: float | None = None):
    """Single reap pass (handy for a cron-style scheduler tick)."""
    return await manager.reap(now=now)


async def reaper_loop(
    manager: _Reapable,
    *,
    interval_s: float,
    stop_event: asyncio.Event,
    on_result: Callable[[object], None] | None = None,
) -> None:
    """Reap every ``interval_s`` until ``stop_event`` is set. One failed pass
    never breaks the loop (logged + retried next tick)."""
    while not stop_event.is_set():
        try:
            result = await manager.reap()
            if on_result is not None:
                on_result(result)
        except Exception as exc:  # noqa: BLE001 - a bad pass must not kill the reaper
            log_warning(f"[scope-sandbox] reaper pass failed: {exc}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass


def start_reaper(
    manager: _Reapable,
    *,
    interval_s: float,
) -> tuple[asyncio.Task, asyncio.Event]:
    """Launch the reaper as a background task. Returns (task, stop_event); set
    the event and await the task to stop. (Call from app startup once a
    process-level manager exists.)"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(reaper_loop(manager, interval_s=interval_s, stop_event=stop_event))
    return task, stop_event
