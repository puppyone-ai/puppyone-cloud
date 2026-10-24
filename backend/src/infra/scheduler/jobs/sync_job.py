"""
Sync execution job for scheduled sync tasks.

APScheduler awaits execute_sync_pull on the API event loop when a scheduled
sync is due (arq's queue client is async-only). It only creates/enqueues a
durable sync run; the sync worker owns execution. Database work runs through
run_blocking, never on the loop.
"""

from contextlib import suppress
from datetime import datetime, timezone

from src.infra.execution.runner import run_blocking
from src.utils.logger import log_error, log_info


def _admit_scheduled_pull(sync_id: str):
    """Return an early result, or the run repository ready for a new run."""
    from src.infra.supabase.client import SupabaseClient
    from src.platform.synchronize.repository import SynchronizeRepository
    from src.platform.synchronize.run_repository import SyncRunRepository

    supabase = SupabaseClient()
    connection = SynchronizeRepository(supabase).get_by_id(sync_id)
    if not connection:
        return {
            "status": "failed",
            "access_point_id": sync_id,
            "error": "Connection not found",
        }
    if connection.direction not in {"inbound", "bidirectional"}:
        return {
            "status": "skipped",
            "access_point_id": sync_id,
            "reason": "not_configured_for_inbound_sync",
        }
    run_repo = SyncRunRepository(supabase)
    active_run = run_repo.get_blocking_active_by_sync(sync_id)
    if active_run:
        return _deduped(sync_id, active_run)
    if connection.status not in {"active", "error"}:
        return {
            "status": "skipped",
            "access_point_id": sync_id,
            "reason": f"status_{connection.status}",
        }
    return run_repo


def _deduped(sync_id: str, run) -> dict:
    return {
        "status": run.status,
        "access_point_id": sync_id,
        "connection_id": sync_id,
        "run_id": run.id,
        "worker_job_id": run.worker_job_id,
        "deduped": True,
        "reason": f"sync_already_{run.status}",
    }


def _record_queue_failure(sync_id: str, error: str) -> None:
    from src.infra.supabase.client import SupabaseClient
    from src.platform.synchronize.repository import SynchronizeRepository

    with suppress(Exception):
        SynchronizeRepository(SupabaseClient()).update_error(sync_id, error)


async def _execute_sync_pull_async(sync_id: str, trigger_type: str = "scheduled") -> dict:
    """
    Queue fresh data pull for a scheduled Integration binding.
    """
    from src.platform.synchronize.arq_client import SyncArqClient

    started_at = datetime.now(timezone.utc)
    log_info(f"[sync-scheduler] Queueing pull for sync {sync_id}")
    admitted = await run_blocking(_admit_scheduled_pull, sync_id)
    if isinstance(admitted, dict):
        return admitted
    run_repo = admitted

    try:
        run, created = await run_blocking(
            run_repo.create_queued_single_lane,
            sync_id,
            trigger_type=trigger_type,
        )
        if not created:
            return _deduped(sync_id, run)
        worker_job_id = await SyncArqClient().enqueue_sync_run(run.id)
        await run_blocking(run_repo.set_worker_job_id, run.id, worker_job_id)
        elapsed_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)

        log_info(f"[sync-scheduler] Pull queued for sync {sync_id} in {elapsed_ms}ms")
        return {
            "status": "queued",
            "access_point_id": sync_id,
            "connection_id": sync_id,
            "run_id": run.id,
            "worker_job_id": worker_job_id,
            "elapsed_ms": elapsed_ms,
        }

    except Exception as e:
        log_error(f"[sync-scheduler] Queueing failed for sync {sync_id}: {e}")
        import traceback

        log_error(f"Traceback: {traceback.format_exc()}")
        await run_blocking(_record_queue_failure, sync_id, str(e))
        return {"status": "failed", "access_point_id": sync_id, "error": str(e)}


async def execute_sync_pull(sync_id: str, trigger_type: str = "scheduled"):
    """APScheduler coroutine job (default AsyncIOExecutor, API event loop)."""
    log_info(f"[sync-scheduler] Scheduler triggered for sync {sync_id}")

    try:
        return await _execute_sync_pull_async(sync_id, trigger_type)
    except Exception as e:
        log_error(f"[sync-scheduler] Failed: {e}")
        return {"status": "failed", "error": str(e)}
