"""Periodic jobs owned by the Version Engine."""

from __future__ import annotations

from src.config import settings
from src.infra.scheduler.service import THREAD_POOL, IntervalJob
from src.version_engine.derived.object_gc_job import process_git_object_gc
from src.version_engine.derived.object_integrity_job import process_object_integrity_scan


def scheduled_jobs() -> list[IntervalJob]:
    jobs: list[IntervalJob] = []
    if settings.VERSION_OBJECT_GC_ENABLED:
        jobs.append(
            IntervalJob(
                id="version-object-gc",
                name="Version Engine Git Object GC",
                run=process_git_object_gc,
                seconds=settings.VERSION_OBJECT_GC_INTERVAL_SECONDS,
                executor=THREAD_POOL,
            )
        )
    if settings.VERSION_INTEGRITY_SCAN_ENABLED:
        jobs.append(
            IntervalJob(
                id="version-object-integrity-scan",
                name="Version Engine Object Integrity Scan",
                run=process_object_integrity_scan,
                seconds=settings.VERSION_INTEGRITY_SCAN_INTERVAL_SECONDS,
                executor=THREAD_POOL,
            )
        )
    return jobs
