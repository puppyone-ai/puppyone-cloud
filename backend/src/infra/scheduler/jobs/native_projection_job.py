"""Consume native repository ref events on the scheduler's async event loop.

Each claimed project is rebuilt off the loop (synchronous Version Engine
work), broadcast to WebSocket subscribers, then finished. A failed rebuild or
broadcast releases the claim without finishing it, so the event is retried.
"""

import uuid

from src.infra.execution.runner import run_blocking
from src.utils.logger import log_error, log_warning
from src.version_engine.derived.native_events import (
    claim_native_events,
    finish_native_event,
    rebuild_projection,
    release_native_event,
)


async def process_native_events(*, repo_manager=None, limit=20) -> int:
    from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container
    from src.version_engine.derived.notifications import NotificationManager

    if repo_manager is None:
        repo_manager = (await run_blocking(build_worker_version_engine_container)).repo_manager
    token = str(uuid.uuid4())
    rows = await run_blocking(claim_native_events, repo_manager, token, limit)
    processed = 0
    for row in rows:
        project = row["project_id"]
        try:
            head, sequence = await run_blocking(rebuild_projection, repo_manager, project, token)
            await NotificationManager.get().broadcast_commit_update(
                project,
                "",
                commit_id=head,
                pushed_by="repository",
                changes=[],
                message="Repository refs changed",
            )
            await run_blocking(finish_native_event, repo_manager, project, token, sequence)
            processed += 1
        except Exception as exc:
            log_warning(f"[native-projection] retry project={project}: {type(exc).__name__}")
        finally:
            await run_blocking(release_native_event, repo_manager, project, token)
    return processed


async def process_native_projection() -> dict:
    try:
        return {"status": "ok", "processed": await process_native_events()}
    except Exception as exc:
        log_error(f"[native-projection] scheduler job failed: {type(exc).__name__}")
        return {"status": "failed", "error": type(exc).__name__}
