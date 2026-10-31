"""Automation and Synchronize are not offered: nothing registers their jobs."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import src.infra.scheduler.service as scheduler_module
from src.platform.access.adapters.agent.config import router


@pytest.mark.asyncio
async def test_starting_the_scheduler_loads_no_agent_or_sync_job(monkeypatch):
    jobs = []

    class FakeScheduler:
        def __init__(self, **_):
            pass

        def start(self):
            return None

        def add_job(self, func, **_):
            jobs.append(func)
            return SimpleNamespace(next_run_time=None)

    async def forbidden(_self):
        raise AssertionError("scheduled Agents and syncs must not be registered")

    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler_module.SchedulerService, "_load_scheduled_agents", forbidden)
    monkeypatch.setattr(scheduler_module.SchedulerService, "_load_scheduled_syncs", forbidden)
    monkeypatch.setattr(scheduler_module.scheduler_settings, "enabled", True)

    await scheduler_module.SchedulerService().start()

    assert scheduler_module.execute_agent_task not in jobs
    assert scheduler_module.execute_sync_pull not in jobs


def update(service, agent, **fields):
    payload = SimpleNamespace(
        name=None,
        icon=None,
        type=fields.get("type"),
        description=None,
        is_default=None,
        trigger_type=fields.get("trigger_type"),
        trigger_config=None,
        task_content=None,
        task_path=None,
        external_config=None,
    )
    return router.update_agent(payload, agent, SimpleNamespace(user_id="user"), service)


@pytest.fixture(autouse=True)
def allowed(monkeypatch):
    monkeypatch.setattr(router, "require_agent_manage_body", Mock())
    monkeypatch.setattr(router, "_to_agent_out", lambda agent: None)


@pytest.mark.parametrize(
    "changes", [{"type": "schedule"}, {"trigger_type": "cron"}], ids=["schedule", "cron"]
)
def test_an_agent_cannot_be_created_as_scheduled(changes):
    service = Mock()
    fields = {"type": "chat", "trigger_type": None, **changes}
    payload = SimpleNamespace(
        project_id="project",
        name="Agent",
        icon=None,
        description=None,
        bash_accesses=[],
        trigger_config=None,
        task_content=None,
        task_path=None,
        external_config=None,
        **fields,
    )
    with pytest.raises(HTTPException) as refused:
        router.create_agent(payload, SimpleNamespace(user_id="user"), service, Mock())
    assert refused.value.status_code == 409
    assert refused.value.detail == {"code": "scheduled_agents_unavailable"}
    service.create_agent.assert_not_called()


@pytest.mark.parametrize(
    "changes", [{"type": "schedule"}, {"trigger_type": "cron"}], ids=["schedule", "cron"]
)
def test_an_agent_cannot_become_scheduled(changes):
    service = Mock()
    agent = SimpleNamespace(id="agent", type="chat", trigger_type="manual")
    with pytest.raises(HTTPException) as refused:
        update(service, agent, **changes)
    assert refused.value.status_code == 409
    service.update_agent.assert_not_called()


def test_an_agent_already_saved_as_scheduled_stays_editable():
    service = Mock()
    agent = SimpleNamespace(id="agent", type="schedule", trigger_type="cron")
    update(service, agent, type="schedule", trigger_type="cron")
    service.update_agent.assert_called_once()
