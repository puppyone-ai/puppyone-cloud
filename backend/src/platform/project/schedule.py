"""Periodic jobs owned by the Project module."""

from __future__ import annotations

from src.config import settings
from src.infra.scheduler.service import IntervalJob
from src.platform.project.deletion_cleanup import process_project_deletion_cleanup
from src.platform.project.initialization_reconciler import (
    process_project_initialization_reconciliation,
)
from src.platform.project.native_projection_job import process_native_projection


def scheduled_jobs() -> list[IntervalJob]:
    jobs: list[IntervalJob] = []
    if settings.VERSION_OUTBOX_ENABLED:
        jobs.append(
            IntervalJob(
                id="native-repository-projection",
                name="Native Repository Projections",
                run=process_native_projection,
                seconds=settings.VERSION_OUTBOX_INTERVAL_SECONDS,
            )
        )
    if settings.PROJECT_DELETION_CLEANUP_ENABLED:
        jobs.append(
            IntervalJob(
                id="project-deletion-cleanup",
                name="Project Object Prefix Cleanup",
                run=process_project_deletion_cleanup,
                seconds=settings.PROJECT_DELETION_CLEANUP_INTERVAL_SECONDS,
            )
        )
    if settings.PROJECT_INITIALIZATION_RECONCILE_ENABLED:
        jobs.append(
            IntervalJob(
                id="project-initialization-reconciler",
                name="Project Root Initialization Reconciler",
                run=process_project_initialization_reconciliation,
                seconds=settings.PROJECT_INITIALIZATION_RECONCILE_INTERVAL_SECONDS,
            )
        )
    return jobs
