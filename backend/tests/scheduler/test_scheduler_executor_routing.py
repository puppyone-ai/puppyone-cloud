from __future__ import annotations

from datetime import datetime

import pytest
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import ThreadPoolExecutor

import src.infra.scheduler.service as scheduler_module


class FakeJob:
    next_run_time = datetime(2026, 6, 4, 9, 0)


class FakeScheduler:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.jobs: list[tuple[object, dict]] = []

    def start(self):
        return None

    def add_job(self, func, **kwargs):
        self.jobs.append((func, kwargs))
        return FakeJob()

    def remove_job(self, job_id):
        return None


@pytest.mark.asyncio
async def test_scheduler_routes_async_jobs_to_asyncio_default(monkeypatch):
    fake_schedulers: list[FakeScheduler] = []

    def fake_scheduler_factory(**kwargs):
        scheduler = FakeScheduler(**kwargs)
        fake_schedulers.append(scheduler)
        return scheduler

    async def noop_loader(self):
        return None

    monkeypatch.setattr(
        scheduler_module,
        "AsyncIOScheduler",
        fake_scheduler_factory,
    )
    monkeypatch.setattr(
        scheduler_module.SchedulerService,
        "_load_scheduled_agents",
        noop_loader,
    )
    monkeypatch.setattr(
        scheduler_module.SchedulerService,
        "_load_scheduled_syncs",
        noop_loader,
    )
    monkeypatch.setattr(scheduler_module.scheduler_settings, "enabled", True)
    monkeypatch.setattr(scheduler_module.settings, "VERSION_OUTBOX_ENABLED", True)
    monkeypatch.setattr(scheduler_module.settings, "VERSION_OBJECT_GC_ENABLED", True)
    monkeypatch.setattr(scheduler_module.settings, "VERSION_INTEGRITY_SCAN_ENABLED", True)
    monkeypatch.setattr(scheduler_module.settings, "SHADOW_SNAPSHOT_REAPER_ENABLED", True)

    service = scheduler_module.SchedulerService()
    await service.start()
    await service.add_agent_job(
        agent_id="agent-1",
        trigger_config={
            "date": "2026-06-04",
            "time": "09:00",
            "repeat_type": "once",
        },
        agent_name="Daily Agent",
    )
    await service.sync_trigger(
        connection_id="conn-1",
        provider="github",
        trigger_config={
            "date": "2026-06-04",
            "time": "09:00",
            "repeat_type": "once",
        },
    )

    scheduler = fake_schedulers[0]
    executors = scheduler.kwargs["executors"]
    assert isinstance(executors["default"], AsyncIOExecutor)
    assert isinstance(executors["threadpool"], ThreadPoolExecutor)

    jobs_by_id = {kwargs["id"]: (func, kwargs) for func, kwargs in scheduler.jobs}

    # Sandbox cleanup moved to the app-scoped durable reaper lifecycle. The
    # deleted in-memory scheduler job must not be reintroduced here.
    assert "sandbox-reaper" not in jobs_by_id

    assert "shadow-snapshot-reaper" not in jobs_by_id
    assert "executor" not in jobs_by_id["native-repository-projection"][1]

    for job_id in [
        "version-object-gc",
        "version-object-integrity-scan",
        "agent-1",
        "sync:conn-1",
    ]:
        assert jobs_by_id[job_id][1]["executor"] == "threadpool"


@pytest.mark.asyncio
async def test_startup_loaders_read_database_off_the_event_loop(monkeypatch):
    import threading

    loop_thread = threading.get_ident()
    reads: list[tuple[str, int]] = []
    registered: list[tuple[str, str]] = []

    def agent_rows():
        reads.append(("agents", threading.get_ident()))
        return [{"id": "agent-1", "config": {"name": "Daily", "trigger": {"type": "cron"}}}]

    def sync_rows():
        reads.append(("syncs", threading.get_ident()))
        return [{"id": "sync-1", "provider": "github", "trigger": {"type": "scheduled"}}]

    async def add_agent_job(self, agent_id, trigger_config, agent_name=""):
        registered.append(("agent", agent_id))

    async def sync_trigger(self, connection_id, provider, trigger_config):
        registered.append(("sync", connection_id))

    monkeypatch.setattr(scheduler_module, "_scheduled_agent_rows", agent_rows)
    monkeypatch.setattr(scheduler_module, "_scheduled_sync_rows", sync_rows)
    monkeypatch.setattr(scheduler_module.SchedulerService, "add_agent_job", add_agent_job)
    monkeypatch.setattr(scheduler_module.SchedulerService, "sync_trigger", sync_trigger)

    service = scheduler_module.SchedulerService()
    service.scheduler = FakeScheduler()
    await service._load_scheduled_agents()
    await service._load_scheduled_syncs()

    assert [name for name, _thread in reads] == ["agents", "syncs"]
    assert loop_thread not in {thread for _name, thread in reads}
    assert registered == [("agent", "agent-1"), ("sync", "sync-1")]
