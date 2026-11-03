"""Worker modules: role selection, sandbox ownership and bounded diagnostics."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.config import settings
from src.platform.access.adapters.agent.runtime import dispatch, worker
from src.platform.workspace.lifecycle import pending_snapshot
from src.platform.workspace.sandbox_worker import SandboxModule
from tests.agent.runtime.test_worker_dispatch import isolate_worker


@pytest.mark.parametrize(
    ("value", "roles"),
    [
        ("agent,sandbox", {"agent", "sandbox"}),
        (" sandbox , agent ", {"agent", "sandbox"}),
        ("agent", {"agent"}),
        ({"sandbox"}, {"sandbox"}),
    ],
)
def test_roles_parse(value, roles):
    assert worker.worker_roles(value) == frozenset(roles)


@pytest.mark.parametrize("value", ["", "api", "agent,api", ","])
def test_unknown_or_empty_roles_are_rejected(value):
    with pytest.raises(ValueError, match="WORKER_ROLES"):
        worker.worker_roles(value)


def test_settings_validate_roles_and_default_to_both():
    from typing import Annotated

    from pydantic import TypeAdapter, ValidationError

    field = type(settings).model_fields["WORKER_ROLES"]
    roles = TypeAdapter(Annotated[field.annotation, *field.metadata])
    assert worker.worker_roles(field.default) == {"agent", "sandbox"}
    assert roles.validate_python("sandbox") == "sandbox"
    for invalid in ["api", "agent,sandbox,agent", "agent;sandbox"]:
        with pytest.raises(ValidationError):
            roles.validate_python(invalid)


def test_identity_names_the_modules_not_the_deployment_role(monkeypatch):
    monkeypatch.setenv("SERVICE_ROLE", "api")  # A shared local .env.
    identity = dispatch.worker_identity("fly", service="worker:agent+sandbox")
    assert identity.service == "worker:agent+sandbox"


def owner_with_lifecycle(served):
    async def serve(*, stop, grace):
        served.set()
        await stop.wait()

    lifecycle = SimpleNamespace(serve=Mock(side_effect=serve), active={}, pending_snapshot=(0, 0.0))
    return SimpleNamespace(reap=AsyncMock(), lifecycle=lifecycle)


class FinishingSupervisor:
    def __init__(self, *args, **kwargs):
        self.outcome = None

    async def run_claim(self, run):
        self.outcome = {"state": "succeeded", "code": "completed"}


def repository_with(runs, claims):
    def claim(**values):
        claims.append(values["worker"]["pool"])
        return runs.pop(0) if runs else None

    return SimpleNamespace(
        client=object(), claim_workbench_run=claim, release_execution=Mock()
    )


@pytest.mark.asyncio
async def test_sandbox_only_worker_owns_lifecycle_and_never_claims(monkeypatch):
    claims, served = [], asyncio.Event()
    isolate_worker(monkeypatch, repository_with([], claims), FinishingSupervisor)
    owner = owner_with_lifecycle(served)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.serve(workbench_owner=owner, stop=stop, roles="sandbox"))
    await asyncio.wait_for(served.wait(), 2)
    for _ in range(50):
        if worker.reap_workspaces.await_count:
            break
        await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert claims == []
    owner.reap.assert_awaited()  # Workbench maintenance.
    worker.reap_workspaces.assert_awaited()  # Session workspace retirement.


@pytest.mark.asyncio
async def test_agent_only_worker_claims_and_never_touches_lifecycle(monkeypatch, caplog):
    run = {
        "id": "run",
        "execution_id": "execution",
        "fence": 2,
        "dispatch_attempts": 1,
        "workbench_id": "workbench",
        "created_at": (datetime.now(UTC) - timedelta(seconds=3)).isoformat(),
    }
    claims = []
    isolate_worker(monkeypatch, repository_with([run], claims), FinishingSupervisor)
    monkeypatch.setattr(settings, "AGENT_WORKER_POOL", "dev-alice")
    monkeypatch.setattr(worker, "STATS_INTERVAL_SECONDS", 0.05)
    owner = owner_with_lifecycle(asyncio.Event())
    stop = asyncio.Event()
    caplog.set_level(logging.INFO, logger=worker.logger.name)
    task = asyncio.create_task(worker.serve(workbench_owner=owner, stop=stop, roles="agent"))
    for _ in range(100):
        if any(
            r.getMessage() == "agent_worker_stats" and r.worker_stats["claimed"]
            for r in caplog.records
        ):
            break
        await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, 5)

    owner.lifecycle.serve.assert_not_called()
    owner.reap.assert_not_awaited()
    worker.reap_workspaces.assert_not_awaited()
    assert claims and set(claims) == {"dev-alice"}

    started = next(r for r in caplog.records if r.getMessage() == "cloud_agent_worker_started")
    assert started.agent_dispatch["service"] == "worker:agent"
    assert started.agent_dispatch["roles"] == ["agent"]
    claimed = next(r for r in caplog.records if r.getMessage() == "cloud_agent_run_claimed")
    fields = claimed.agent_dispatch
    assert fields["run_id"] == "run" and fields["fence"] == 2 and fields["pool"] == "dev-alice"
    assert fields["dispatch_attempts"] == 1 and fields["workbench"] is True
    assert 2.5 <= fields["queue_wait_seconds"] < 30
    stats = [r.worker_stats for r in caplog.records if r.getMessage() == "agent_worker_stats"]
    totals = {key: sum(s[key] for s in stats) for key in ("claimed", "supervisor_failed")}
    assert totals == {"claimed": 1, "supervisor_failed": 0}
    assert any(s["outcomes"] == {"succeeded:completed": 1} for s in stats)
    assert all(s["module"] == "agent" and s["pool"] == "dev-alice" for s in stats)


def test_queue_wait_is_omitted_when_admission_time_is_unknown():
    assert worker.queue_wait_seconds({}) is None
    assert worker.queue_wait_seconds({"created_at": "not a time"}) is None


def test_pending_snapshot_reports_count_and_oldest_age():
    now = datetime.now(UTC)
    rows = [
        {"updated_at": (now - timedelta(seconds=40)).isoformat()},
        {"updated_at": (now - timedelta(seconds=5)).isoformat()},
        {"updated_at": None},
    ]
    count, oldest = pending_snapshot(rows)
    assert count == 3 and 39 <= oldest < 45
    assert pending_snapshot([]) == (0, 0.0)


def test_sandbox_report_is_bounded_and_resets(caplog):
    lifecycle = SimpleNamespace(active={"a": 1}, pending_snapshot=(2, 12.5))
    module = SandboxModule(
        SimpleNamespace(reap=AsyncMock(), lifecycle=lifecycle), pool="default", slow_passes=[]
    )
    module.completed["workbench_maintenance"] = 3
    module.failed["session_workspace_retirement"] = 1
    caplog.set_level(logging.INFO)
    assert module.report() == {
        "module": "sandbox",
        "pool": "default",
        "open_operations_active": 1,
        "open_requests_pending": 2,
        "oldest_open_request_seconds": 12.5,
        "passes_completed": {"workbench_maintenance": 3},
        "passes_failed": {"session_workspace_retirement": 1},
    }
    assert not module.completed and not module.failed


def test_logging_bridge_keeps_dispatch_reports_and_drops_other_extras():
    from loguru import logger as loguru_logger

    from src.utils.logging_setup import InterceptHandler

    captured = []
    sink = loguru_logger.add(lambda message: captured.append(message.record), level="INFO")
    try:
        record = logging.LogRecord("cloud_agent", logging.INFO, __file__, 1, "probe", None, None)
        record.agent_dispatch = {"run_id": "run", "pool": "default"}
        record.worker_stats = {"module": "agent", "claimed": 1}
        record.authorization = "must-never-log-this"
        InterceptHandler().emit(record)
    finally:
        loguru_logger.remove(sink)
    extra = captured[-1]["extra"]
    assert extra["agent_dispatch"] == {"run_id": "run", "pool": "default"}
    assert extra["worker_stats"] == {"module": "agent", "claimed": 1}
    assert "authorization" not in extra
