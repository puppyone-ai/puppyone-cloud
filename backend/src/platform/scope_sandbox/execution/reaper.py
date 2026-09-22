"""Cross-worker cleanup for durable ephemeral execution sessions."""

from __future__ import annotations

import asyncio
import time

from .store import ExecutionSessionStore, durable_execution_store
from src.utils.logger import log_warning


async def run_execution_reaper_once(
    store: ExecutionSessionStore | None = None,
    *,
    now: float | None = None,
    docker_timeout_s: float = 600,
    e2b_timeout_s: float = 1800,
) -> int:
    store = store or durable_execution_store()
    current = time.time() if now is None else now
    expired: list[tuple[str, str]] = []
    for provider, timeout in (("docker", docker_timeout_s), ("e2b", e2b_timeout_s)):
        expired.extend(
            (provider, row.session_id)
            for row in store.list_provider(provider)
            if current - row.last_activity >= timeout
        )

    stopped = 0
    for provider, session_id in expired:
        row = store.get(session_id)
        if row is not None and not row.resource_id:
            # A worker died after atomically claiming the session but before a
            # provider resource was created. There is nothing external to stop.
            store.delete(session_id)
            stopped += 1
            continue
        # Stop is idempotent. Multiple API replicas may race here safely; the
        # durable row is the shared source of truth.
        if provider == "e2b":
            from .e2b_sandbox import E2BSandbox

            impl = E2BSandbox(session_store=store)
        else:
            from .docker_sandbox import DockerSandbox

            impl = DockerSandbox(session_store=store)
        result = await impl.stop(session_id)
        if result.get("success"):
            stopped += 1
    return stopped


def start_execution_reaper(*, interval_s: int = 60):
    stop_event = asyncio.Event()

    async def loop() -> None:
        while not stop_event.is_set():
            try:
                await run_execution_reaper_once()
            except Exception as exc:
                # A transient provider/DB failure must not permanently kill the
                # recovery loop; the next pass retries the durable record.
                log_warning(f"[SandboxExecutionReaper] pass failed; will retry: {exc}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(1, interval_s))
            except asyncio.TimeoutError:
                continue

    return asyncio.create_task(loop()), stop_event
