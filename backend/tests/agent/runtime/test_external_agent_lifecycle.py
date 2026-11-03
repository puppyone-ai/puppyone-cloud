"""An external Agent's machine may sleep and disappear; its work may not."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

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
        saved=Mock(),
    )
    service = SimpleNamespace(
        repository=repository,
        channels=SimpleNamespace(invalidate=AsyncMock()),
        channel_factory=Mock(return_value=host),
        billing=SimpleNamespace(finish_session=AsyncMock(return_value=True)),
        git=SimpleNamespace(save=save or AsyncMock(return_value={"status": "saved", "tip": "c"})),
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
    service.git.save.side_effect = lambda row: order.append("save") or {"status": "saved"}
    runtime.pause_resource.side_effect = lambda host: order.append("pause") or SimpleNamespace(
        receipt=lambda resource: {}
    )
    await subject.pause(AGENT)
    assert order == ["save", "pause"]
    service.repository.saved.assert_called_once_with(AGENT)


async def test_a_failed_save_keeps_the_machine_running_for_the_next_attempt():
    subject, service, runtime = lifecycle(AsyncMock(side_effect=RuntimeError("Git unavailable")))
    with pytest.raises(RuntimeError):
        await subject.pause(AGENT)
    runtime.pause_resource.assert_not_awaited()
    service.repository.saved.assert_not_called()
    service.repository.transition.assert_not_called()


async def test_a_lost_machine_cannot_block_its_pause_and_stays_unsaved():
    subject, service, runtime = lifecycle(AsyncMock(side_effect=WorkerLost("gone")))
    await subject.pause(AGENT)
    runtime.pause_resource.assert_awaited_once()
    service.repository.saved.assert_not_called()


async def test_revoked_access_pauses_without_claiming_a_save():
    denied = WorkspaceError(403, {"code": "access_denied"})
    subject, service, runtime = lifecycle(AsyncMock(side_effect=denied))
    await subject.pause(AGENT)
    runtime.pause_resource.assert_awaited_once()
    service.repository.saved.assert_not_called()


async def test_a_waiting_merge_sleeps_without_being_called_saved():
    subject, service, runtime = lifecycle(AsyncMock(return_value={"status": "merging"}))
    await subject.pause(AGENT)
    runtime.pause_resource.assert_awaited_once()
    service.repository.saved.assert_not_called()


async def test_the_internal_machine_is_never_saved_for_the_owner():
    subject, service, runtime = lifecycle()
    await subject.pause(INTERNAL)
    service.git.save.assert_not_awaited()
    runtime.pause_resource.assert_awaited_once()


def machine(postgres, args):
    owner = running(postgres, args)
    _, workspace = admitted(postgres, args, owner)
    return workspace


def saved(postgres, workspace):
    return postgres.row(
        f"SELECT public.cloud_workbench_saved({literal(workspace['id'])},"
        f"{literal(workspace['generation'])}) AS row"
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
