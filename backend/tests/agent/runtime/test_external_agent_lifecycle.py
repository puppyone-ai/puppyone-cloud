"""An external Agent's machine may sleep and disappear; its work may not."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.sandbox import WorkspaceDiskLost
from src.platform.workspace import lifecycle as module
from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.execution.worker_port import WorkerLost
from src.platform.workspace.lifecycle import WorkbenchLifecycle
from tests.agent.runtime.conftest import literal
from tests.agent.runtime.test_agent_links import (
    admitted,
    set_state,
)
from tests.agent.runtime.test_live_workbench_control import running

AGENT = {
    "id": "w",
    "user_id": "owner",
    "project_id": "project",
    "generation": "g",
    "version": 1,
    "state": "pausing",
    "binding": {"provider": "docker"},
    "agent_registration_id": "agent",
}
INTERNAL = {**AGENT, "agent_registration_id": None}


def lifecycle(save=None):
    runtime = SimpleNamespace(
        pause_resource=AsyncMock(
            return_value=SimpleNamespace(receipt=lambda resource: {"preserved": True})
        ),
        aclose=AsyncMock(),
    )
    host = SimpleNamespace(runtime=runtime, resource={})
    repository = SimpleNamespace(
        transition=Mock(return_value=AGENT),
        progress=Mock(),
        save_outcome=Mock(),
        disk_lost=Mock(),
        reclaim_claim=Mock(),
    )
    service = SimpleNamespace(
        repository=repository,
        channels=SimpleNamespace(invalidate=AsyncMock()),
        channel_factory=Mock(return_value=host),
        billing=SimpleNamespace(finish_session=AsyncMock(return_value=True)),
        git=SimpleNamespace(snapshot=save or AsyncMock(return_value={"status": "saved", "tip": "c"})),
        providers=Mock(),
        authorization=None,
        transport=None,
        filesystem=None,
        context=Mock(),
        check=AsyncMock(),
    )
    return WorkbenchLifecycle(service), service, runtime


@pytest.fixture(autouse=True)
def metered(monkeypatch):
    monkeypatch.setattr(module.settings, "RUNTIME_METERING_MODE", "disabled")


async def test_an_agents_work_reaches_its_branch_before_the_machine_sleeps():
    subject, service, runtime = lifecycle()
    order = []
    service.git.snapshot.side_effect = lambda row: order.append("save") or {
        "status": "saved",
        "tip": "c",
    }
    runtime.pause_resource.side_effect = lambda host: order.append("pause") or SimpleNamespace(
        receipt=lambda resource: {}
    )
    await subject.pause(AGENT)
    assert order == ["save", "pause"]
    service.repository.save_outcome.assert_called_once_with(AGENT, "saved", error=None, oid="c")


def assert_slept(service, runtime, status, error):
    runtime.pause_resource.assert_awaited_once()
    service.repository.save_outcome.assert_called_once_with(AGENT, status, error=error, oid=None)
    assert service.repository.transition.call_args.args[1] == "paused"


async def test_a_save_that_keeps_failing_never_holds_the_pause(monkeypatch):
    monkeypatch.setattr(module, "SAVE_RETRY_SECONDS", 0)
    save = AsyncMock(side_effect=RuntimeError("Workbench Git snapshot failed"))
    subject, service, runtime = lifecycle(save)
    await subject.pause(AGENT)
    assert save.await_count == module.SAVE_ATTEMPTS
    assert_slept(service, runtime, "failed", "workbench_save_failed")


async def test_a_passing_outage_is_saved_on_a_later_attempt(monkeypatch):
    monkeypatch.setattr(module, "SAVE_RETRY_SECONDS", 0)
    save = AsyncMock(side_effect=[RuntimeError("Git unavailable"), {"status": "saved", "tip": "c"}])
    subject, service, runtime = lifecycle(save)
    await subject.pause(AGENT)
    service.repository.save_outcome.assert_called_once_with(AGENT, "saved", error=None, oid="c")
    runtime.pause_resource.assert_awaited_once()


async def test_a_save_that_hangs_runs_out_of_budget_and_the_pause_goes_on(monkeypatch):
    monkeypatch.setattr(module, "SAVE_BUDGET_SECONDS", 0.01)

    async def hang(row):
        await asyncio.Event().wait()

    subject, service, runtime = lifecycle(hang)
    await subject.pause(AGENT)
    assert_slept(service, runtime, "failed", "workbench_save_timeout")


async def test_a_rejected_save_is_recorded_rather_than_lost():
    subject, service, runtime = lifecycle(AsyncMock(return_value={"status": "rejected"}))
    await subject.pause(AGENT)
    assert_slept(service, runtime, "rejected", "workbench_save_rejected")


async def test_a_lost_machine_cannot_block_its_pause_and_stays_unsaved():
    subject, service, runtime = lifecycle(AsyncMock(side_effect=WorkerLost("gone")))
    await subject.pause(AGENT)
    assert_slept(service, runtime, "skipped", "workbench_save_compute_lost")


async def test_revoked_access_pauses_without_claiming_a_save():
    denied = WorkspaceError(403, {"code": "access_denied"})
    subject, service, runtime = lifecycle(AsyncMock(side_effect=denied))
    await subject.pause(AGENT)
    assert_slept(service, runtime, "skipped", "workbench_save_no_access")


async def test_a_waiting_merge_sleeps_without_being_called_saved():
    subject, service, runtime = lifecycle(AsyncMock(return_value={"status": "merging"}))
    await subject.pause(AGENT)
    assert_slept(service, runtime, "merging", "workbench_save_merging")


async def test_the_internal_machine_is_never_saved_for_the_owner():
    subject, service, runtime = lifecycle()
    await subject.pause(INTERNAL)
    service.git.snapshot.assert_not_awaited()
    service.repository.save_outcome.assert_not_called()
    runtime.pause_resource.assert_awaited_once()


async def test_a_disk_lost_during_a_pause_ends_the_workspace_instead_of_retrying():
    subject, service, runtime = lifecycle()
    runtime.pause_resource.side_effect = WorkspaceDiskLost("gone")
    await subject.pause(INTERNAL)
    service.repository.disk_lost.assert_called_once_with(INTERNAL)
    service.repository.transition.assert_not_called()


def started_ago(seconds):
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def test_a_pause_expires_only_past_its_limit():
    limit = module.PAUSE_LIMIT_SECONDS
    assert module.WorkbenchLifecycle._pause_expired({"pausing_since": started_ago(limit + 1)})
    assert not module.WorkbenchLifecycle._pause_expired({"pausing_since": started_ago(limit - 60)})
    assert not module.WorkbenchLifecycle._pause_expired({"pausing_since": None})


async def test_an_abandoned_pause_returns_a_running_machine_to_service():
    subject, service, runtime = lifecycle()
    runtime.running_connection = AsyncMock()
    await subject._abandon_pause(AGENT)
    assert service.repository.transition.call_args.args[1] == "running"
    assert service.repository.progress.call_args.args[1] == "ready"


async def test_an_abandoned_pause_that_cannot_be_checked_is_held_not_retried():
    subject, service, runtime = lifecycle()
    runtime.running_connection = AsyncMock(side_effect=RuntimeError("provider unreachable"))
    await subject._abandon_pause(AGENT)
    assert service.repository.transition.call_args.args[1] == "retained"


def retained(subject, runtime, **resource):
    runtime.resolve = AsyncMock(return_value=[{**resource}])
    return {**AGENT, "state": "retained", "resource": resource}


async def test_retained_with_its_disk_confirmed_goes_back_to_paused():
    subject, service, runtime = lifecycle()
    row = retained(subject, runtime, resource_id="m", initialized=True, volume_id="v")
    await subject._preserve_failed_start(row)
    assert service.repository.transition.call_args.args[1] == "paused"


async def test_retained_without_a_working_directory_is_held_and_reported(caplog):
    subject, service, runtime = lifecycle()
    row = retained(subject, runtime, resource_id="m", volume_id="v")
    await subject._preserve_failed_start(row)
    service.repository.transition.assert_not_called()


async def test_retained_with_nothing_created_goes_back_to_retired():
    subject, service, _ = lifecycle()
    await subject._preserve_failed_start({**AGENT, "state": "retained", "resource": None})
    assert service.repository.transition.call_args.args[1] == "retired"


async def test_retained_with_an_unconfirmed_resource_is_never_reset():
    subject, service, runtime = lifecycle()
    runtime.resolve = AsyncMock(return_value=[{"resource_id": "late"}])
    row = {
        **AGENT,
        "state": "retained",
        "resource": {"resource_id": "m", "allocation_pending": True},
    }
    with pytest.raises(module.WorkerDisconnected):
        await subject._preserve_failed_start(row)
    service.repository.transition.assert_not_called()


def machine(postgres, args):
    owner = running(postgres, args)
    _, workspace = admitted(postgres, args, owner)
    return workspace


def saved(postgres, workspace, status="saved", error="NULL"):
    return postgres.row(
        f"SELECT public.cloud_workbench_save_outcome({literal(workspace['id'])},"
        f"{literal(workspace['generation'])},'{status}',{error}) AS row"
    )


def unsaved(postgres, workspace):
    return postgres.row(
        f"SELECT unpublished_changes FROM cloud_workbenches WHERE id='{workspace['id']}'"
    )["unpublished_changes"]


@pytest.mark.integration
def test_only_a_pausing_workspace_can_be_marked_saved(postgres, submitted):
    args, _ = submitted
    workspace = machine(postgres, args)
    assert unsaved(postgres, workspace) is True
    saved(postgres, workspace)
    assert unsaved(postgres, workspace) is True  # Running: a connection could still write.
    set_state(postgres, workspace, "pausing")
    saved(postgres, workspace)
    assert unsaved(postgres, workspace) is False


@pytest.mark.integration
def test_a_failed_save_is_recorded_and_keeps_the_work_marked_unsaved(postgres, submitted):
    args, _ = submitted
    workspace = machine(postgres, args)
    set_state(postgres, workspace, "pausing")
    saved(postgres, workspace, "failed", "'workbench_save_failed'")
    row = postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{workspace['id']}'")
    assert row["save_status"] == "failed"
    assert row["save_error"] == "workbench_save_failed"
    assert row["unpublished_changes"] is True


def transition(postgres, row, state):
    return postgres.row(
        f"""SELECT public.cloud_workbench_transition({literal(row['id'])},
        {literal(row['generation'])},{row['version']},{literal(row['operation_id'])},
        '{state}') AS row"""
    )["row"]


def resource_of(workspace, **fields):
    return {
        "provider": "docker",
        "workbench_id": workspace["id"],
        "workbench_generation": workspace["generation"],
        "resource_id": "machine",
        "allocation_pending": False,
        **fields,
    }


def leased(postgres, workspace, state, **fields):
    value = literal(json.dumps(resource_of(workspace, **fields)))
    postgres.sql(
        "ALTER TABLE cloud_workbenches DISABLE TRIGGER USER;"
        f"""UPDATE cloud_workbenches SET state='{state}',operation_id=gen_random_uuid(),
        operation_until=now()+interval '1 minute',resource={value}::jsonb,
        binding='{{"provider":"docker","artifact":"fixture"}}'::jsonb
        WHERE id='{workspace['id']}';"""
        "ALTER TABLE cloud_workbenches ENABLE TRIGGER USER;"
    )
    return postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{workspace['id']}'")


@pytest.mark.integration
@pytest.mark.parametrize(
    "start,end", [("pausing", "running"), ("pausing", "retained"), ("retained", "paused")]
)
def test_every_stuck_state_has_a_way_out(postgres, submitted, start, end):
    args, _ = submitted
    row = leased(postgres, machine(postgres, args), start)
    assert transition(postgres, row, end)["state"] == end


@pytest.mark.integration
def test_entering_a_pause_starts_its_clock_and_leaving_clears_it(postgres, submitted):
    args, _ = submitted
    workspace = machine(postgres, args)
    row = leased(postgres, workspace, "running")
    assert transition(postgres, row, "pausing")["pausing_since"] is not None
    row = postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{workspace['id']}'")
    assert transition(postgres, row, "running")["pausing_since"] is None


@pytest.mark.integration
def test_retained_work_is_never_retired_while_a_disk_may_hold_it(postgres, submitted):
    args, _ = submitted
    workspace = machine(postgres, args)
    row = leased(postgres, workspace, "retained", allocation_pending=True, volume_id="disk")
    with pytest.raises(RuntimeError, match="workbench_transition_invalid"):
        transition(postgres, row, "retired")


@pytest.mark.integration
def test_retained_with_nothing_created_can_be_retired(postgres, submitted):
    args, _ = submitted
    workspace = machine(postgres, args)
    row = leased(postgres, workspace, "retained", allocation_pending=True)
    assert transition(postgres, row, "retired")["state"] == "retired"


@pytest.mark.integration
def test_a_disk_lost_during_a_pause_retires_the_workspace(postgres, submitted):
    args, _ = submitted
    row = leased(postgres, machine(postgres, args), "pausing")
    lost = postgres.row(
        f"""SELECT public.cloud_workbench_disk_lost({literal(row['id'])},
        {literal(row['generation'])},{row['version']},{literal(row['operation_id'])}) AS row"""
    )["row"]
    assert lost["state"] == "retired"
    assert lost["operation_error"] == "workbench_disk_lost"


@pytest.mark.integration
def test_a_failed_reclaim_of_a_live_owner_can_be_retried_but_retained_cannot(postgres, submitted):
    args, _ = submitted
    workspace = machine(postgres, args)

    def claim(state):
        row = leased(postgres, workspace, state)
        postgres.sql(
            "ALTER TABLE cloud_workbenches DISABLE TRIGGER USER;"
            f"UPDATE cloud_workbenches SET operation_until=now()-interval '1 second' WHERE id='{row['id']}';"
            "ALTER TABLE cloud_workbenches ENABLE TRIGGER USER;"
        )
        return postgres.row(
            f"""SELECT public.cloud_workbench_reclaim_claim({literal(row['id'])},
            {literal(row['generation'])},{row['version']}) AS claim"""
        )["claim"]

    assert claim("cleanup_pending")["acquired"] is True
    assert claim("retained")["acquired"] is False


@pytest.mark.integration
def test_unsaved_work_is_never_reclaimed(postgres, submitted):
    args, _ = submitted
    workspace = machine(postgres, args)
    postgres.sql(
        "ALTER TABLE cloud_workbenches DISABLE TRIGGER USER;"
        f"""UPDATE cloud_workbenches SET state='paused',paused_at=now()-interval '30 days'
        WHERE id='{workspace['id']}';"""
        "ALTER TABLE cloud_workbenches ENABLE TRIGGER USER;"
    )
    current = postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{workspace['id']}'")
    claim = postgres.row(
        f"""SELECT public.cloud_workbench_idle_claim({literal(current['id'])},
        {literal(current['generation'])},{current['version']},'retire') AS claim"""
    )["claim"]
    assert claim["acquired"] is False
    postgres.sql(
        "ALTER TABLE cloud_workbenches DISABLE TRIGGER USER;"
        f"UPDATE cloud_workbenches SET unpublished_changes=false WHERE id='{workspace['id']}';"
        "ALTER TABLE cloud_workbenches ENABLE TRIGGER USER;"
    )
    claim = postgres.row(
        f"""SELECT public.cloud_workbench_idle_claim({literal(current['id'])},
        {literal(current['generation'])},{current['version']},'retire') AS claim"""
    )["claim"]
    assert claim["acquired"] is True
