"""The consuming channel proves readiness; cached facts never grant access."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from postgrest.exceptions import APIError

from src.platform.workspace.admission import AdmissionSnapshots
from src.platform.workspace.execution.worker_port import WorkerDisconnected
from src.platform.workspace.service import WorkbenchService


def fixture():
    row = dict(
        id="workspace",
        user_id="owner",
        project_id="project",
        generation="one",
        compute_epoch=1,
        state="resuming",
        binding={"provider": "fly"},
        resource={"id": "machine"},
        open_requested=True,
        operation_phase="checking_files",
        owner_deleted_at=None,
        open_deadline=(datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
        operation_until=(datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
    )
    context = {"workbench": row, "facts": {}, "revision": {"project": "project", "version": 1}}
    host = SimpleNamespace(admitted_row=row)

    @asynccontextmanager
    async def borrow(_):
        yield host

    service = WorkbenchService(
        Mock(),
        Mock(operation=AsyncMock(return_value={"entries": []})),
        Mock(check_session=AsyncMock()),
        repository=Mock(),
        authorization=Mock(),
    )
    service.context = Mock(return_value=(context, object()))
    service.channels = Mock(borrow=Mock(side_effect=borrow))
    service.repository.activity.return_value = row
    service.repository.finish_open.return_value = {
        **row,
        "state": "running",
        "open_requested": False,
    }
    return service, row, context


@pytest.mark.asyncio
async def test_status_completes_existing_intent_with_the_consuming_root_and_no_duplicate_read():
    service, _, _ = fixture()
    result, root = await service.opening.status("owner", "project")
    assert result["state"] == "running" and root == {"entries": []}
    assert service.context.call_count == 2  # entry and final current authority
    service.filesystem.operation.assert_awaited_once()
    service.repository.finish_open.assert_called_once()
    service.repository.request_open.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["paused", "running", "retired"])
async def test_status_without_handoff_never_creates_resumes_or_connects(state):
    service, row, _ = fixture()
    row.update(state=state, open_requested=False)
    result, root = await service.opening.status("owner", "project")
    assert result is row and root is None
    service.channels.borrow.assert_not_called()
    service.repository.finish_open.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [{"compute_epoch": 2}, {"resource": {}}, {"binding": {}}, {"generation": "other"}]
)
async def test_replaced_runtime_cannot_publish_old_readiness(change):
    service, row, context = fixture()
    service.context.side_effect = [
        (context, object()),
        ({**context, "workbench": {**row, **change}}, object()),
    ]
    with pytest.raises(WorkerDisconnected):
        await service.opening.status("owner", "project")
    service.repository.finish_open.assert_not_called()


@pytest.mark.asyncio
async def test_permission_revoked_during_readiness_returns_no_root():
    service, _, context = fixture()
    service.context.side_effect = [(context, object()), PermissionError("revoked")]
    with pytest.raises(PermissionError):
        await service.opening.status("owner", "project")
    service.repository.finish_open.assert_not_called()


@pytest.mark.asyncio
async def test_healthy_read_uses_two_current_database_fences():
    service, row, context = fixture()
    row.update(state="running", open_requested=False)
    service.snapshots.put("owner", "project", context)
    assert await service.file_operation(
        "owner", "project", "one", {"action": "list", "path": ""}
    ) == {"entries": []}
    service.repository.activity.assert_called_once_with(row, context["revision"], write=False)
    service.context.assert_called_once()  # final response authority; activity fences cached facts


@pytest.mark.asyncio
async def test_changed_revision_reauthorizes_before_any_file_access():
    service, row, context = fixture()
    row.update(state="running", open_requested=False)
    service.snapshots.put("owner", "project", context)
    service.repository.activity.side_effect = APIError(
        {"code": "P0001", "message": "agent_context_changed", "details": None, "hint": None}
    )
    service.context.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError):
        await service.file_operation("owner", "project", "one", {"action": "list", "path": ""})
    service.channels.borrow.assert_not_called()
    assert service.snapshots.get("owner", "project") is None


def test_snapshot_bounds_expiry_and_identity(monkeypatch):
    clock = [10]
    monkeypatch.setattr("src.platform.workspace.admission.time.monotonic", lambda: clock[0])
    snapshots = AdmissionSnapshots(limit=1, seconds=5)
    snapshots.put("one", "project", {"facts": {"role": "viewer"}})
    snapshots.get("one", "project")["facts"]["role"] = "admin"
    assert snapshots.get("one", "project")["facts"]["role"] == "viewer"
    assert snapshots.get("two", "project") is None
    snapshots.put("two", "project", {})
    assert snapshots.get("one", "project") is None
    clock[0] += 6
    assert snapshots.get("two", "project") is None


@pytest.mark.parametrize(
    "code,message,accepted",
    [
        ("P0001", "agent_context_changed", True),
        ("PGRST202", "missing function", False),
        ("P0001", "unrelated failure", False),
        ("42501", "permission denied", False),
    ],
)
def test_opening_preflight_only_accepts_the_expected_database_rejection(code, message, accepted):
    from src.platform.workspace.repository import WorkbenchRepository

    repo = WorkbenchRepository(client=Mock())
    repo.command = Mock(
        side_effect=APIError({"code": code, "message": message, "details": None, "hint": None})
    )
    if accepted:
        repo.command.side_effect = [
            APIError({"code": code, "message": message, "details": None, "hint": None}),
            APIError(
                {
                    "code": "22023",
                    "message": "workbench_open_claim_invalid",
                    "details": None,
                    "hint": None,
                }
            ),
        ]
        repo.verify_opening_contract()
    else:
        with pytest.raises(APIError):
            repo.verify_opening_contract()
