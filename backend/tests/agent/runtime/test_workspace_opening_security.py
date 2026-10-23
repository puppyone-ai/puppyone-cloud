"""Reuse removes duplicate reads, never the current authorization/epoch fence."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.workspace.execution.worker_port import WorkerDisconnected
from src.platform.workspace.service import WorkbenchService
from tests.agent.runtime.test_workspace_readiness import lifecycle


@pytest.mark.asyncio
async def test_warm_open_skips_provider_probe_but_rechecks_authority_after_readiness():
    owner, row, provider = lifecycle("running")
    owner.channels.is_healthy.return_value = True
    events = []
    owner.ready = AsyncMock(side_effect=lambda _: events.append("ready"))

    async def revoked(_):
        events.append("authorize")
        raise PermissionError("Revoked while connecting")

    owner.check = AsyncMock(side_effect=revoked)
    with pytest.raises(PermissionError):
        await owner.request("user", "project", row)
    assert events == ["ready", "authorize"]
    provider.preflight.assert_not_awaited()
    provider.validate.assert_called_once()
    owner.repository.activity.assert_called_once()


@pytest.mark.asyncio
async def test_disconnected_running_workspace_still_checks_provider():
    owner, row, provider = lifecycle("running")
    owner.channels.is_healthy.return_value = False
    owner.ready = AsyncMock()
    await owner.request("user", "project", row)
    provider.preflight.assert_awaited_once()
    owner.check.assert_awaited_once_with(row)


@pytest.mark.asyncio
async def test_existing_resource_needs_binding_validation_not_duplicate_allocation_preflight():
    owner, row, provider = lifecycle("paused")
    row["resource"] = {"resource_id": "existing"}
    await owner.preflight(row)
    provider.validate.assert_called_once_with("pinned", row["resource"])
    provider.preflight.assert_not_awaited()  # actual resume/inspect still authenticates with Fly


def service():
    row = dict(
        id="default",
        user_id="owner",
        project_id="project",
        is_default=True,
        generation="generation",
        compute_epoch=1,
        state="running",
        owner_deleted_at=None,
        binding={"provider": "fixture"},
        resource={"machine": "one"},
    )
    instance = WorkbenchService.__new__(WorkbenchService)
    instance.context = Mock(return_value=({"workbench": dict(row)}, object()))
    instance.repository = Mock()
    instance.billing = Mock(check_session=AsyncMock())
    return instance, row


@pytest.mark.asyncio
async def test_default_check_uses_one_authorized_snapshot():
    instance, row = service()
    await instance.check(row)
    instance.context.assert_called_once()
    instance.repository.get.assert_not_called()
    instance.billing.check_session.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"id": "replacement"},
        {"user_id": "other"},
        {"project_id": "other"},
        {"generation": "next"},
        {"compute_epoch": 2},
        {"state": "paused"},
        {"owner_deleted_at": "deleted"},
        {"binding": {}},
        {"resource": {"machine": "two"}},
    ],
)
async def test_current_identity_and_runtime_changes_fail_closed(change):
    instance, row = service()
    instance.context.return_value = ({"workbench": {**row, **change}}, object())
    with pytest.raises(WorkerDisconnected):
        await instance.check(row)
    instance.billing.check_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_automation_checks_its_own_row_not_the_default_workspace():
    instance, row = service()
    automation = {**row, "id": "automation", "is_default": False}
    instance.repository.get.return_value = automation
    await instance.check(automation)
    instance.repository.get.assert_called_once_with("automation")


@pytest.mark.asyncio
async def test_revoked_authority_cannot_be_replaced_by_a_cached_connection():
    instance, row = service()
    instance.context.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError):
        await instance.check(row)
    instance.billing.check_session.assert_not_awaited()
