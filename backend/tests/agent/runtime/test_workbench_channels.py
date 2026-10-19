"""Transport reuse never grants ownership, replays writes or crosses an epoch."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform.scope_sandbox.execution.workbench_host import WorkbenchChannels
from src.platform.scope_sandbox.execution.worker_port import WorkerDisconnected

pytestmark = pytest.mark.asyncio


def row(identity="workspace", epoch=1):
    return dict(
        id=identity,
        generation="generation",
        compute_epoch=epoch,
        binding={"provider": "fixture"},
        resource={"resource_id": identity},
    )


@pytest.fixture
async def pool():
    made = []

    def factory(binding, *, purpose):
        assert purpose == "files"
        channel = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(), healthy=lambda: True)
        made.append(channel)
        return channel

    cache = WorkbenchChannels(factory, limit=2, idle_seconds=60)
    cache.made = made
    yield cache
    await cache.close()
    assert all(channel.close.await_count == 1 for channel in made)


async def test_reuses_one_channel_and_serializes_requests(pool):
    active = 0

    async def operation():
        nonlocal active
        async with pool.borrow(row()):
            active += 1
            assert active == 1
            await asyncio.sleep(0)
            active -= 1

    await asyncio.gather(*(operation() for _ in range(20)))
    assert len(pool.made) == 1
    pool.made[0].connect.assert_awaited_once_with()


async def test_isolates_workspaces_and_reopens_changed_epoch(pool):
    async with pool.borrow(row()) as first:
        pass
    async with pool.borrow(row("other")) as other:
        assert other is not first
    async with pool.borrow(row(epoch=2)) as resumed:
        assert resumed is not first and resumed is not other
    first.close.assert_awaited_once()


async def test_uncertain_write_is_not_replayed(pool):
    calls = 0
    with pytest.raises(ConnectionError):
        async with pool.borrow(row()) as failed:
            calls += 1
            raise ConnectionError("reply lost after write")
    assert calls == 1
    failed.close.assert_awaited_once()
    async with pool.borrow(row()) as fresh:
        assert fresh is not failed


async def test_cancel_discards_channel(pool):
    entered = asyncio.Event()

    async def blocked():
        async with pool.borrow(row()):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(blocked())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pool.made[0].close.assert_awaited_once()


async def test_capacity_is_bounded_with_concurrent_admission(pool):
    async with pool.borrow(row("one")), pool.borrow(row("two")):
        with pytest.raises(WorkerDisconnected, match="capacity"):
            async with pool.borrow(row("three")):
                pytest.fail("Exceeded live connection capacity")
        assert len(pool.entries) == 2
    async with pool.borrow(row("three")):
        assert len(pool.entries) == 2


async def test_invalidation_rejects_queued_old_epoch(pool):
    entered = asyncio.Event()

    async def queued():
        entered.set()
        async with pool.borrow(row()):
            pytest.fail("Retired binding was reused")

    async with pool.borrow(row()):
        pending = asyncio.create_task(queued())
        await entered.wait()
        invalidation = asyncio.create_task(pool.invalidate("workspace"))
        await asyncio.sleep(0)
    with pytest.raises(WorkerDisconnected, match="retired"):
        await pending
    await invalidation


async def test_idle_connections_close_without_provider_lifecycle(pool):
    pool.idle_seconds = 0.01
    async with pool.borrow(row()) as channel:
        pass
    async with asyncio.timeout(1):
        while pool.entries:
            await asyncio.sleep(0.01)
    channel.close.assert_awaited_once()


async def test_dead_reader_reconnects_before_next_operation(pool):
    async with pool.borrow(row()) as first:
        first.healthy = lambda: False
    async with pool.borrow(row()) as second:
        assert first is not second
    first.close.assert_awaited_once()
