"""An interrupted opening reaches the caller with a code, never as a bare 500.

Opening a workspace crosses the database, the provider and a connection to the
machine. Before this contract, a dropped connection, a 35-second readiness
timeout or a moved authorization revision during GET/POST /workbench escaped as
"Internal Server Error", so Desktop could only say "Cloud Computer couldn't be
opened" and nobody could count which interruption it was.
"""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.exception_handler import http_exception_handler
from src.platform.access.adapters.api.workbench_router import (
    get_workbench_service,
    router,
    workspace_error_handler,
)
from src.platform.auth.dependencies import get_current_user
from src.platform.sandbox import WorkerDisconnected, WorkerLost, WorkspaceDiskLost
from src.platform.workspace.errors import WorkspaceError, opening_failure
from src.platform.workspace.execution.runtime_contract import WorkspaceUnavailable
from src.platform.workspace.lifecycle import WorkbenchLifecycle


def sql_refusal(code, message):
    return APIError({"code": code, "message": message, "details": None, "hint": None})


# (raised inside the opening, HTTP status, code, retryable)
INTERRUPTIONS = [
    (
        WorkerDisconnected("Workspace changed during opening"),
        503,
        "workbench_connection_unavailable",
        True,
    ),
    (ConnectionResetError("peer reset"), 503, "workbench_connection_unavailable", True),
    (TimeoutError(), 503, "workbench_connection_timeout", True),
    (WorkerLost("compute is gone"), 503, "workbench_preparing", True),
    (WorkspaceDiskLost("disk is gone"), 503, "workbench_disk_lost", True),
    (
        WorkspaceUnavailable("workbench_protocol_mismatch"),
        503,
        "workbench_protocol_mismatch",
        False,
    ),
    (
        WorkspaceUnavailable("workbench_connection_unavailable", retryable=True),
        503,
        "workbench_connection_unavailable",
        True,
    ),
    (PermissionError("revoked"), 403, "workbench_access_denied", False),
    (sql_refusal("P0001", "agent_context_changed"), 409, "workbench_context_changed", True),
    (sql_refusal("55000", "workbench_fenced"), 409, "workbench_operation_replaced", True),
    (sql_refusal("55000", "workbench_not_running"), 409, "workbench_not_running", True),
    (sql_refusal("42501", "workbench_unavailable"), 409, "workbench_unavailable", False),
]


def application(status=None, request_open=None):
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(WorkspaceError, workspace_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="user")
    service = SimpleNamespace(
        opening=SimpleNamespace(status=status or AsyncMock()),
        request_open=request_open or AsyncMock(),
    )
    app.dependency_overrides[get_workbench_service] = lambda: service
    return TestClient(app, raise_server_exceptions=False)


def call(stage, error):
    if stage == "status":
        client = application(status=AsyncMock(side_effect=error))
        return client.get("/api/v1/projects/project/workbench?wait_seconds=20")
    client = application(request_open=AsyncMock(side_effect=error))
    return client.post("/api/v1/projects/project/workbench")


@pytest.mark.parametrize("stage", ["status", "open"])
@pytest.mark.parametrize(("error", "status", "code", "retryable"), INTERRUPTIONS)
def test_an_interrupted_opening_returns_its_code(stage, error, status, code, retryable):
    response = call(stage, error)
    assert response.status_code == status
    assert response.json()["data"] == {"code": code, "retryable": retryable}


@pytest.mark.parametrize("stage", ["status", "open"])
def test_a_structured_refusal_keeps_its_contract(stage):
    response = call(stage, WorkspaceError(409, {"code": "workbench_generation_changed"}))
    assert response.status_code == 409
    assert response.json()["data"] == {"code": "workbench_generation_changed"}


@pytest.mark.parametrize("stage", ["status", "open"])
def test_billing_and_auth_refusals_keep_their_own_status(stage):
    response = call(stage, HTTPException(402, {"code": "entitlement_required"}))
    assert response.status_code == 402
    assert response.json()["data"] == {"code": "entitlement_required"}


@pytest.mark.parametrize("stage", ["status", "open"])
def test_an_unclassified_failure_is_still_a_server_error(stage):
    # Hiding a real bug as a retryable refusal would make it look transient.
    assert call(stage, RuntimeError("invariant broken")).status_code == 500


def test_every_failure_is_logged_with_its_code_stage_and_request(caplog):
    caplog.set_level(logging.WARNING, logger="src.platform.access.adapters.api.workbench_router")
    call("status", TimeoutError())
    call("open", RuntimeError("invariant broken"))
    records = [
        r.workbench_open for r in caplog.records if r.getMessage() == "workbench_open_failed"
    ]
    assert [(r["stage"], r["code"], r["status"]) for r in records] == [
        ("status", "workbench_connection_timeout", 503),
        ("open", "unclassified", 500),
    ]
    assert all({"project_id", "retryable", "error_type", "request_id"} <= set(r) for r in records)


def test_a_successful_opening_keeps_its_response():
    row = {
        "id": "w",
        "generation": "g",
        "project_id": "project",
        "state": "running",
        "target_ref": "r",
    }
    client = application(
        status=AsyncMock(return_value=(row, {"entries": []})),
        request_open=AsyncMock(return_value=row),
    )
    status = client.get("/api/v1/projects/project/workbench")
    assert status.status_code == 200 and status.json()["entries"] == []
    assert client.post("/api/v1/projects/project/workbench").status_code == 200


def test_already_described_errors_are_not_reclassified():
    assert opening_failure(WorkspaceError(404, {"code": "workbench_not_found"})) is None
    assert opening_failure(HTTPException(402, "plan")) is None
    assert opening_failure(RuntimeError("unknown")) is None


def lifecycle_with(request_open):
    row = dict(
        id="workspace",
        user_id="user",
        project_id="project",
        generation="g",
        compute_epoch=1,
        operation_id="operation",
        open_requested=False,
        state="paused",
        open_deadline=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        binding={"provider": "third", "artifact": "pinned"},
        resource=None,
    )
    provider = SimpleNamespace(
        name="third",
        capabilities=SimpleNamespace(shared_workspace=True, native_files=True),
        validate=Mock(),
        preflight=AsyncMock(),
        aclose=AsyncMock(),
    )
    service = SimpleNamespace(
        repository=Mock(request_open=request_open),
        authorization=Mock(),
        context=Mock(return_value=({"revision": {"v": 1}, "workbench": row}, object())),
        transport=Mock(),
        filesystem=Mock(),
        billing=Mock(),
        channel_factory=Mock(),
        providers=lambda _: provider,
        channels=Mock(),
        check=AsyncMock(),
    )
    return WorkbenchLifecycle(service), service, row


@pytest.mark.asyncio
async def test_a_revision_change_during_preflight_is_decided_again_on_fresh_facts():
    request_open = Mock(
        side_effect=[
            sql_refusal("P0001", "agent_context_changed"),
            {"state": "paused", "open_requested": True},
        ]
    )
    owner, service, _ = lifecycle_with(request_open)
    assert (await owner.request("user", "project"))["open_requested"]
    assert service.context.call_count == 2
    assert request_open.call_count == 2


@pytest.mark.asyncio
async def test_a_second_revision_change_is_reported_not_retried_forever():
    request_open = Mock(side_effect=sql_refusal("P0001", "agent_context_changed"))
    owner, _, _ = lifecycle_with(request_open)
    with pytest.raises(APIError):
        await owner.request("user", "project")
    assert request_open.call_count == 2


@pytest.mark.asyncio
async def test_other_refusals_are_not_retried():
    request_open = Mock(side_effect=sql_refusal("42501", "workbench_unavailable"))
    owner, _, _ = lifecycle_with(request_open)
    with pytest.raises(APIError):
        await owner.request("user", "project")
    assert request_open.call_count == 1


@pytest.mark.asyncio
async def test_a_failed_handoff_leaves_the_opening_for_the_next_request_to_finish():
    # Marking the durable operation failed would send the start through
    # failed-start preservation (retained); a non-terminal error would make the
    # next poll fail at once. The operation stays as it was instead.
    from tests.agent.runtime.test_workspace_consumer_opening import fixture

    service, _, _ = fixture()
    service.filesystem.operation = AsyncMock(
        side_effect=[WorkerDisconnected("machine connection dropped"), {"entries": []}]
    )
    with pytest.raises(WorkerDisconnected):
        await service.opening.status("owner", "project")
    service.repository.progress.assert_not_called()
    service.repository.finish_open.assert_not_called()

    row, root = await service.opening.status("owner", "project")
    assert row["state"] == "running" and root == {"entries": []}
    service.repository.finish_open.assert_called_once()
    service.repository.progress.assert_not_called()
