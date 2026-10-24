"""Cross-worker cleanup for durable ephemeral execution sessions.

An unretained ephemeral Pi worker records its provider resource in the
execution session store and refreshes ``last_activity`` while it runs. When a
worker dies before its run owner retains the resource, this reaper stops the
idle resource through the provider's ``cleanup`` and then forgets the record.

Only providers with a bounded provider-side lifetime are reaped here. Fly
records lack the app and volume identity that ``cleanup`` requires, so they stay
owned by the Workspace lifecycle reapers.

The same loop retires machines left by the removed Scope Sandbox product
(``legacy_scope``).
"""

from __future__ import annotations

import asyncio
import time

from src.infra.execution.runner import run_blocking
from src.utils.logger import log_warning

from .legacy_scope import retire_legacy_scope_sandboxes_once
from .registry import runtime_provider
from .store import ExecutionSession, ExecutionSessionStore, durable_execution_store


def _resource(row: ExecutionSession) -> dict:
    return {
        "provider": row.provider,
        "resource_id": row.resource_id,
        "session_id": row.session_id,
        "project_id": row.project_id,
    }


async def _cleanup(row: ExecutionSession) -> None:
    provider = runtime_provider(row.provider)
    try:
        await provider.cleanup(_resource(row))
    finally:
        await provider.aclose()


async def run_execution_reaper_once(
    store: ExecutionSessionStore | None = None,
    *,
    now: float | None = None,
    docker_timeout_s: float = 600,
    e2b_timeout_s: float = 1800,
) -> int:
    store = store or durable_execution_store()
    current = time.time() if now is None else now
    expired: list[str] = []
    for provider, timeout in (("docker", docker_timeout_s), ("e2b", e2b_timeout_s)):
        expired.extend(
            row.session_id
            for row in await run_blocking(store.list_provider, provider)
            if current - row.last_activity >= timeout
        )

    stopped = 0
    for session_id in expired:
        # Re-read: the owner may have retained or refreshed the row meanwhile.
        row = await run_blocking(store.get, session_id)
        if row is None:
            continue
        if not row.resource_id:
            # A worker died after atomically claiming the session but before a
            # provider resource was created. There is nothing external to stop.
            await run_blocking(store.delete, session_id)
            stopped += 1
            continue
        # Cleanup is idempotent, so API replicas may race safely; the durable
        # row is the shared source of truth and survives a failed attempt.
        try:
            await _cleanup(row)
        except Exception as exc:
            log_warning(
                f"[SandboxExecutionReaper] cleanup of {row.provider} session failed; "
                f"will retry: {type(exc).__name__}"
            )
            continue
        await run_blocking(store.delete, session_id)
        stopped += 1
    return stopped


def start_sandbox_reaper(*, interval_s: int = 60):
    stop_event = asyncio.Event()

    async def loop() -> None:
        while not stop_event.is_set():
            # Independent passes: one failing never skips the other. A transient
            # provider/DB failure must not kill the loop; durable rows retry.
            for name, reap in (
                ("SandboxExecutionReaper", run_execution_reaper_once),
                ("LegacyScopeSandbox", retire_legacy_scope_sandboxes_once),
            ):
                try:
                    await reap()
                except Exception as exc:
                    log_warning(f"[{name}] pass failed; will retry: {exc}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(1, interval_s))
            except TimeoutError:
                continue

    return asyncio.create_task(loop()), stop_event
