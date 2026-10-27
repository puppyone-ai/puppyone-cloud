"""Overlap is safe only within the same authorized, live opening attempt."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.opening_wait import OpeningObservers
from tests.agent.runtime.test_workspace_consumer_opening import fixture
from tests.agent.runtime.test_workspace_readiness import lifecycle


@pytest.mark.asyncio
async def test_claim_contains_authority_and_progress_without_two_more_database_roundtrips():
    owner, row, _ = lifecycle()
    owner._start = AsyncMock()
    await owner.execute(row)
    owner.repository.begin_open.assert_called_once_with(row)
    owner.service.authorization.authorize_facts.assert_called_once()
    owner.service.context.assert_not_called()
    owner.repository.progress.assert_not_called()
    owner._start.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoked_claim_fails_before_provider_preflight_or_start():
    owner, row, provider = lifecycle()
    owner.service.authorization.authorize_facts.side_effect = PermissionError("revoked")
    owner._start = AsyncMock()
    await owner.execute(row)
    provider.preflight.assert_not_awaited()
    owner._start.assert_not_awaited()


@pytest.mark.asyncio
async def test_preparation_overlaps_resume_and_only_same_attempt_can_consume_it():
    service, row, _ = fixture()
    row.update(operation_phase="connecting", operation_id="owned")
    prepared = asyncio.Event()
    connection = object()

    async def prepare(_):
        prepared.set()
        return connection

    service.opening._prepare_connection = AsyncMock(side_effect=prepare)
    observed = []

    @asynccontextmanager
    async def borrow(current, **options):
        assert current["operation_phase"] == "checking_files"
        observed.append(options)
        yield object()

    service.channels.borrow = Mock(side_effect=borrow)
    task = asyncio.create_task(service.opening.status("owner", "project", wait_seconds=2))
    await asyncio.wait_for(prepared.wait(), 1)
    service.channels.borrow.assert_not_called()  # Machine is still resuming.
    row["operation_phase"] = "checking_files"
    result, root = await task
    assert result["state"] == "running" and root == {"entries": []}
    assert observed == [{"connection": connection}]
    service.repository.finish_open.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [{"operation_id": "replacement"}, {"compute_epoch": 2}, {"generation": "two"}]
)
async def test_changed_attempt_discards_preparation(change):
    service, row, _ = fixture()
    row.update(operation_phase="connecting", operation_id="owned")
    prepared = asyncio.Event()

    async def prepare(_):
        prepared.set()
        return object()

    service.opening._prepare_connection = prepare
    task = asyncio.create_task(service.opening.status("owner", "project", wait_seconds=2))
    await asyncio.wait_for(prepared.wait(), 1)
    row.update(operation_phase="checking_files", **change)
    await task
    service.channels.borrow.assert_called_once_with(row)  # normal, re-inspected connection


@pytest.mark.asyncio
async def test_revocation_during_observation_cancels_preparation_and_returns_no_files():
    service, row, _ = fixture()
    row.update(operation_phase="connecting", operation_id="owned")
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def prepare(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    service.opening._prepare_connection = prepare
    task = asyncio.create_task(service.opening.status("owner", "project", wait_seconds=2))
    await asyncio.wait_for(started.wait(), 1)
    service.context.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError):
        await task
    assert cancelled.is_set()
    service.channels.borrow.assert_not_called()


@pytest.mark.asyncio
async def test_observers_coalesce_bound_capacity_and_cancel_only_the_last_consumer():
    observers = OpeningObservers(limit=1)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    create = Mock(side_effect=operation)
    one = asyncio.create_task(observers.observe(("owner", "project"), create))
    await started.wait()
    two = asyncio.create_task(observers.observe(("owner", "project"), create))
    await asyncio.sleep(0)
    create.assert_called_once()
    with pytest.raises(WorkspaceError):
        await observers.observe(("other", "project"), create)
    one.cancel()
    await asyncio.gather(one, return_exceptions=True)
    assert not cancelled.is_set()
    two.cancel()
    await asyncio.gather(two, return_exceptions=True)
    assert cancelled.is_set() and not observers.entries
    await observers.close()


@pytest.mark.asyncio
async def test_no_intent_never_prepares_route_or_wakes_compute_even_when_wait_requested():
    service, row, _ = fixture()
    row.update(state="paused", open_requested=False)
    service.opening._prepare_connection = AsyncMock()
    assert await service.opening.status("owner", "project", wait_seconds=20) == (row, None)
    service.opening._prepare_connection.assert_not_awaited()
    service.repository.request_open.assert_not_called()


@pytest.mark.asyncio
async def test_failed_speculation_uses_normal_inspected_consumer_connection():
    service, row, _ = fixture()
    row.update(operation_phase="connecting", operation_id="owned")
    started = asyncio.Event()

    async def prepare(_):
        started.set()
        raise ConnectionError("route not yet available")

    service.opening._prepare_connection = prepare
    task = asyncio.create_task(service.opening.status("owner", "project", wait_seconds=2))
    await asyncio.wait_for(started.wait(), 1)
    row["operation_phase"] = "checking_files"
    result, _ = await task
    assert result["state"] == "running"
    service.channels.borrow.assert_called_once_with(row)


@pytest.mark.asyncio
async def test_wait_budget_returns_pending_without_changing_durable_operation():
    service, row, _ = fixture()
    row["operation_phase"] = "queued"
    result, root = await service.opening.status("owner", "project", wait_seconds=0.01)
    assert result is row and root is None
    service.repository.finish_open.assert_not_called()
    service.channels.borrow.assert_not_called()
