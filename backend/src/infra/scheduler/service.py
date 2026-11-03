"""
Generic APScheduler wrapper: run a function on a trigger.

It knows nothing about the jobs it runs. Each module declares its own jobs and
the composition layer passes them to `start`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.infra.scheduler.config import scheduler_settings
from src.utils.logger import log_error, log_info, log_warning

THREAD_POOL = "threadpool"


@dataclass(frozen=True)
class IntervalJob:
    """A periodic job. Modules declare these; the composition layer collects them."""

    id: str
    name: str
    run: Callable[[], Any]
    seconds: int
    executor: str | None = None


class SchedulerService:
    """
    Service for managing APScheduler.

    Responsibilities:
    - Start/stop the scheduler
    - Register the jobs it is given
    - Parse trigger configuration into APScheduler triggers
    """

    _instance: Optional["SchedulerService"] = None

    def __init__(self):
        self.scheduler: AsyncIOScheduler | None = None
        self._started = False

    @classmethod
    def get_instance(cls) -> "SchedulerService":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self, jobs: Sequence[IntervalJob] = ()):
        """Initialize and start the scheduler."""
        if not scheduler_settings.enabled:
            log_info("⏭️  Scheduler disabled (SCHEDULER_ENABLED=false)")
            return

        if self._started:
            log_warning("Scheduler already started")
            return

        log_info("⏰ Starting APScheduler...")

        # Configure executors. The default executor must remain asyncio-aware so
        # APScheduler awaits coroutine jobs instead of running them in a thread.
        executors = {
            "default": AsyncIOExecutor(),
            THREAD_POOL: ThreadPoolExecutor(scheduler_settings.max_workers),
        }

        # Configure job defaults
        job_defaults = {
            "coalesce": scheduler_settings.coalesce,
            "max_instances": 1,
            "misfire_grace_time": scheduler_settings.misfire_grace_time,
        }

        # Create scheduler
        self.scheduler = AsyncIOScheduler(
            executors=executors,
            job_defaults=job_defaults,
            timezone=scheduler_settings.timezone,
        )

        # Start the scheduler
        self.scheduler.start()
        self._started = True

        for job in jobs:
            self.add_job(
                job.run,
                IntervalTrigger(seconds=job.seconds),
                job_id=job.id,
                name=job.name,
                executor=job.executor,
            )

        log_info(f"✅ APScheduler started with {scheduler_settings.max_workers} workers")

    async def shutdown(self):
        """Gracefully shutdown the scheduler."""
        if self.scheduler and self._started:
            log_info("⏰ Shutting down APScheduler...")
            self.scheduler.shutdown(wait=True)
            self._started = False
            log_info("✅ APScheduler stopped")

    @property
    def running(self) -> bool:
        return self.scheduler is not None and self._started

    def add_job(
        self,
        func: Callable[..., Any],
        trigger,
        *,
        job_id: str,
        name: str,
        args: Sequence[Any] = (),
        executor: str | None = None,
    ) -> Job:
        """Register a job. Without an executor it runs on the asyncio default."""
        options: dict[str, Any] = {"args": list(args)} if args else {}
        if executor:
            options["executor"] = executor
        return self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            name=name,
            replace_existing=True,
            **options,
        )

    def remove_job(self, job_id: str) -> bool:
        """Remove a job; a job that does not exist is not an error."""
        if not self.scheduler:
            return False

        try:
            self.scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    def parse_trigger(self, config: dict):
        """
        Parse trigger configuration and return APScheduler trigger.

        Supports:
        - Cron expression: {"schedule": "0 9 * * *"}
        - Simple time + repeat: {"time": "09:00", "repeat_type": "daily", "date": "2026-01-30"}
        """
        # If explicit cron schedule provided
        if "schedule" in config:
            try:
                return CronTrigger.from_crontab(
                    config["schedule"], timezone=config.get("timezone", scheduler_settings.timezone)
                )
            except Exception as e:
                log_error(f"Invalid cron expression '{config['schedule']}': {e}")
                return None

        # Parse simple time/date/repeat format from frontend
        time_str = config.get("time", "09:00")  # HH:MM format
        date_str = config.get("date")  # YYYY-MM-DD format
        repeat_type = config.get("repeat_type", "once")  # once, daily, weekly
        timezone = config.get("timezone", scheduler_settings.timezone)

        try:
            hour, minute = map(int, time_str.split(":"))
        except (ValueError, AttributeError):
            hour, minute = 9, 0  # Default to 9:00 AM

        if repeat_type == "once":
            # One-time execution
            if date_str:
                try:
                    run_date = datetime.fromisoformat(f"{date_str}T{time_str}:00")
                    return DateTrigger(run_date=run_date, timezone=timezone)
                except Exception as e:
                    log_error(f"Invalid date '{date_str}': {e}")
                    return None
            else:
                log_warning("One-time trigger without date, skipping")
                return None

        elif repeat_type == "daily":
            # Every day at specified time
            return CronTrigger(hour=hour, minute=minute, timezone=timezone)

        elif repeat_type == "weekly":
            # Every week on the same day
            if date_str:
                try:
                    dt = datetime.fromisoformat(date_str)
                    day_of_week = dt.weekday()  # 0=Monday, 6=Sunday
                    return CronTrigger(
                        day_of_week=day_of_week, hour=hour, minute=minute, timezone=timezone
                    )
                except Exception as e:
                    log_error(f"Invalid date for weekly trigger '{date_str}': {e}")
                    return None
            else:
                # Default to Monday if no date specified
                return CronTrigger(day_of_week=0, hour=hour, minute=minute, timezone=timezone)

        else:
            log_warning(f"Unknown repeat_type: {repeat_type}")
            return None


# Global instance getter
def get_scheduler_service() -> SchedulerService:
    """Get the global scheduler service instance."""
    return SchedulerService.get_instance()
