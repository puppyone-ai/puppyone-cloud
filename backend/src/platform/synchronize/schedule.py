"""Periodic recovery and per-binding pull triggers for Integration sync.

Synchronize is not offered, so nothing calls `load_scheduled_syncs` at startup.
"""

from __future__ import annotations

from apscheduler.job import Job

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.infra.scheduler.service import THREAD_POOL, IntervalJob, get_scheduler_service
from src.infra.supabase.client import SupabaseClient
from src.platform.synchronize.run_repository import SyncRunRepository
from src.platform.synchronize.scheduled_pull import execute_sync_pull
from src.utils.logger import log_error, log_info, log_warning


def process_sync_run_reaper() -> dict:
    try:
        repo = SyncRunRepository(SupabaseClient())
        recovered = repo.recover_stale_active_runs(
            lease_seconds=settings.SYNC_RUN_LEASE_SECONDS,
            limit=settings.SYNC_RUN_REAPER_MAX_PER_RUN,
        )
        return {
            "status": "ok",
            "recovered": len(recovered),
            "run_ids": [run.id for run in recovered],
        }
    except Exception as exc:  # noqa: BLE001
        log_error(f"[sync-run-reaper] scheduler job failed: {exc}")
        return {"status": "failed", "error": str(exc)}


def scheduled_jobs() -> list[IntervalJob]:
    if not settings.SYNC_RUN_REAPER_ENABLED:
        return []
    return [
        IntervalJob(
            id="sync-run-reaper",
            name="Integration Sync Run Lease Reaper",
            run=process_sync_run_reaper,
            seconds=settings.SYNC_RUN_REAPER_INTERVAL_SECONDS,
            executor=THREAD_POOL,
        )
    ]


def sync_trigger(
    connection_id: str,
    provider: str = "",
    trigger_config: dict | None = None,
) -> Job | None:
    """Unified trigger registration for scheduled Connect rows.

    Connect routers should call this single function instead of directly
    manipulating scheduler jobs.

    Args:
        connection_id: The connection / sync row ID.
        provider: Source provider name (e.g. "gmail", "github").
        trigger_config: Scheduling config dict, or *None* to remove.

    Returns:
        The registered ``Job``, or ``None`` if removed / skipped.
    """
    scheduler = get_scheduler_service()
    if not scheduler.running:
        return None

    job_id = f"sync:{connection_id}"
    scheduler.remove_job(job_id)

    if not trigger_config:
        return None

    trigger = scheduler.parse_trigger(trigger_config)
    if not trigger:
        log_warning(f"Invalid trigger config for {connection_id}: {trigger_config}")
        return None

    # Coroutine job on the default AsyncIOExecutor: arq's client is async.
    job = scheduler.add_job(
        execute_sync_pull,
        trigger,
        job_id=job_id,
        name=f"Sync: {provider} ({connection_id[:8]})",
        args=[connection_id],
    )

    next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "N/A"
    log_info(f"📅 Registered trigger for {provider} ({connection_id[:8]}), next: {next_run}")
    return job


def _scheduled_sync_rows() -> list[dict]:
    result = (
        SupabaseClient()
        .client.table("synchronize_bindings")
        .select("id, provider, trigger_type, trigger_config, status")
        .eq("status", "active")
        .execute()
    )
    syncs = [
        {
            "id": row["id"],
            "provider": row.get("provider", ""),
            "trigger": {
                "type": row.get("trigger_type"),
                **(row.get("trigger_config") or {}),
            },
        }
        for row in (result.data or [])
    ]
    return [sync for sync in syncs if sync["trigger"].get("type") == "scheduled"]


async def load_scheduled_syncs() -> None:
    """Load all syncs with trigger type 'scheduled' and register jobs."""
    if not get_scheduler_service().scheduler:
        return

    try:
        # Reads the database off the event loop so startup never blocks it.
        scheduled = await run_blocking(_scheduled_sync_rows)

        log_info(f"Found {len(scheduled)} scheduled syncs to load")

        for sync_row in scheduled:
            sync_trigger(
                connection_id=sync_row["id"],
                provider=sync_row.get("provider", ""),
                trigger_config=sync_row.get("trigger") or {},
            )

        if scheduled:
            log_info(f"Loaded {len(scheduled)} sync polling jobs")

    except Exception as e:
        log_error(f"Failed to load scheduled syncs: {e}")
