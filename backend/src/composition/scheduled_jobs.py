"""Every periodic job this deployment runs."""

from __future__ import annotations

from src.infra.scheduler.service import IntervalJob
from src.platform.imports import schedule as imports
from src.platform.project import schedule as project
from src.platform.synchronize import schedule as synchronize
from src.platform.upload import schedule as upload
from src.version_engine.derived import schedule as version_engine


def scheduled_jobs() -> list[IntervalJob]:
    # Scheduled Agents and per-binding sync pulls are not offered, so neither is loaded here.
    return [
        *version_engine.scheduled_jobs(),
        *project.scheduled_jobs(),
        *synchronize.scheduled_jobs(),
        *imports.scheduled_jobs(),
        *upload.scheduled_jobs(),
    ]
