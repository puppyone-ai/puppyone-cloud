"""Failure injection at the provider-independent workspace readiness boundary."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.workspace.execution.runtime_contract import (
    WorkspaceUnavailable,
    validate_runtime,
)
from src.platform.workspace.lifecycle import WorkbenchLifecycle


def lifecycle(state="paused"):
    row = dict(
        id="workspace",
        user_id="user",
        project_id="project",
        generation="generation",
        compute_epoch=1,
        operation_id="operation",
        open_requested=True,
        state=state,
        open_deadline=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        binding={"provider": "third", "artifact": "pinned"},
        resource=None,
    )
    repository = Mock(
        activity=Mock(return_value=row),
        get=Mock(return_value=row),
        acquire=Mock(return_value={"acquired": True, "workbench": row}),
        begin_open=Mock(return_value={"acquired": True, "workbench": row, "facts": {}}),
    )
    provider = SimpleNamespace(
        name="third",
        capabilities=SimpleNamespace(shared_workspace=True, native_files=True),
        validate=Mock(),
        preflight=AsyncMock(),
        aclose=AsyncMock(),
    )
    service = SimpleNamespace(
        repository=repository,
        authorization=Mock(),
        context=Mock(return_value=({"revision": {}}, object())),
        transport=Mock(),
        filesystem=Mock(),
        billing=SimpleNamespace(detach_session=AsyncMock()),
        channel_factory=Mock(),
        providers=lambda _: provider,
        channels=Mock(),
        check=AsyncMock(),
    )
    return WorkbenchLifecycle(service), row, provider


@pytest.mark.asyncio
async def test_http_open_only_records_intent():
    owner, row, provider = lifecycle()
    owner.repository.request_open.return_value = {**row, "open_requested": True}
    result = await owner.request("user", "project", row)
    assert result["open_requested"]
    owner.channel_factory.assert_not_called()
    provider.preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_running_resource_must_pass_real_file_readiness():
    owner, row, _ = lifecycle("running")
    owner.ready = AsyncMock(side_effect=WorkspaceUnavailable("workbench_protocol_mismatch"))
    with pytest.raises(WorkspaceUnavailable, match="workbench_protocol_mismatch"):
        await owner.request("user", "project", row)
    owner.repository.request_open.assert_not_called()


@pytest.mark.asyncio
async def test_bad_worker_credentials_publish_failure_without_creating_resource():
    owner, row, provider = lifecycle("allocating")
    provider.preflight.side_effect = WorkspaceUnavailable("workbench_provider_authentication")
    owner._start = AsyncMock()
    await owner.execute(row)
    owner._start.assert_not_awaited()
    owner.repository.progress.assert_called_with(
        row, "failed", "workbench_provider_authentication", False
    )
    assert row["state"] == "allocating"  # Connection failure is not proof of lost files.


@pytest.mark.asyncio
async def test_canceled_owner_leaves_durable_intent_for_successor():
    owner, row, _ = lifecycle("allocating")
    started = asyncio.Event()

    async def start(*_):
        started.set()
        await asyncio.Event().wait()

    owner._start = start
    task = asyncio.create_task(owner.execute(row))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not any(call.args[1] == "failed" for call in owner.repository.progress.call_args_list)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"runtime_protocol": 1, "file_protocol": 1, "capabilities": [{}]},
        {
            "runtime_protocol": 1,
            "file_protocol": 1,
            "capabilities": ["workspace_lifecycle", "native_files", "file_watch"],
            "build_id": None,
        },
    ],
)
def test_incompatible_or_malformed_runtime_is_explicit(value):
    with pytest.raises(WorkspaceUnavailable, match="workbench_protocol_mismatch") as error:
        validate_runtime(value)
    assert not error.value.retryable


def test_status_reports_elapsed_deadline_even_without_a_live_worker():
    from src.platform.access.adapters.api.workbench_router import public_workbench

    _, row, _ = lifecycle("allocating")
    row.update(
        open_requested=True,
        target_ref="refs/heads/workspace",
        open_deadline=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    result = public_workbench(row)
    assert result["operation"]["error"] == "workbench_start_timeout"
    assert not result["operation"]["pending"]
    assert row["open_requested"] and result["state"] == "allocating"
