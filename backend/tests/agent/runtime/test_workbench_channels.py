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


async def test_reuses_one_channel_without_serializing_callers(pool):
    active = 0
    peak = 0

    async def operation():
        nonlocal active, peak
        async with pool.borrow(row()):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1

    await asyncio.gather(*(operation() for _ in range(20)))
    assert peak == 20
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


async def test_cancel_preserves_other_channel_users(pool):
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
    pool.made[0].close.assert_not_awaited()
    async with pool.borrow(row()) as reused:
        assert reused is pool.made[0]


async def test_capacity_is_bounded_with_concurrent_admission(pool):
    async with pool.borrow(row("one")), pool.borrow(row("two")):
        with pytest.raises(WorkerDisconnected, match="capacity"):
            async with pool.borrow(row("three")):
                pytest.fail("Exceeded live connection capacity")
        assert len(pool.entries) == 2
    async with pool.borrow(row("three")):
        assert len(pool.entries) == 2


async def test_invalidation_closes_active_transport_and_next_borrow_reconnects(pool):
    async with pool.borrow(row()) as active:
        await pool.invalidate("workspace")
        active.close.assert_awaited_once()
        async with pool.borrow(row(epoch=2)) as fresh:
            assert fresh is not active


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


async def test_watch_backlog_cannot_block_responses_or_other_subscribers():
    from src.platform.scope_sandbox.execution.control_channel import ControlChannel
    from src.platform.scope_sandbox.execution.workbench_host import WorkbenchChannel

    channel = object.__new__(WorkbenchChannel)
    channel.purpose, channel.generation = "files", "generation"
    slow, fast = asyncio.Queue(maxsize=2), asyncio.Queue(maxsize=2)
    channel.subscribers = {slow, fast}
    for index in range(10):
        await channel._event(
            {"type": "workspace_changed", "generation": "generation", "paths": [str(index)]}
        )
        assert (await fast.get())["paths"] == [str(index)]
    assert slow.qsize() <= 2
    assert any(slow.get_nowait().get("reset") for _ in range(slow.qsize()))
    channel.controls, channel.buffer, channel.output_sequence = {}, "", 0
    future = asyncio.get_running_loop().create_future()
    channel.controls["request"] = future
    await ControlChannel._output(
        channel, '{"sequence":1,"type":"control_result","id":"request","result":{"saved":true}}\n'
    )
    assert await future == {"saved": True}
    await channel._event({"type": "disconnected"})
    assert (await fast.get())["type"] == "disconnected"


async def test_revocation_while_waiting_does_not_disclose_changed_paths():
    from contextlib import asynccontextmanager

    from fastapi import HTTPException

    from src.platform.scope_sandbox.workbench_service import WorkbenchService

    queue = asyncio.Queue()
    waiting = asyncio.Event()
    revoked = False

    async def bound(*args):
        if revoked:
            raise HTTPException(403)
        waiting.set()

    @asynccontextmanager
    async def subscribe():
        yield queue

    @asynccontextmanager
    async def files(*args, **kwargs):
        assert kwargs == {"watch": True}
        yield SimpleNamespace(subscribe=subscribe)

    service = SimpleNamespace(
        files=files,
        bound=bound,
        filesystem=SimpleNamespace(subscribe=lambda channel: channel.subscribe()),
    )
    events = WorkbenchService.events(service, "user", "project", "generation")
    assert (await anext(events))["reset"]
    waiting.clear()
    next_event = asyncio.create_task(anext(events))
    await waiting.wait()
    revoked = True
    queue.put_nowait({"type": "workspace_changed", "paths": ["private.txt"]})
    with pytest.raises(HTTPException) as error:
        await next_event
    assert error.value.status_code == 403
    await events.aclose()


async def test_subscriptions_share_watch_and_release_only_the_last_listener():
    from src.platform.scope_sandbox.execution.workbench_host import WorkbenchChannel

    channel = object.__new__(WorkbenchChannel)
    channel.subscribers, channel.watch_lock = set(), asyncio.Lock()
    channel.watch_started = False
    channel.control = AsyncMock()
    channel.healthy = lambda: True
    async with channel.subscribe() as first:
        async with channel.subscribe() as second:
            assert first is not second
            channel.control.assert_awaited_once_with("watch")
        channel.control.assert_awaited_once_with("watch")
    assert [call.args for call in channel.control.await_args_list] == [("watch",), ("unwatch",)]


async def test_watch_admission_does_not_register_workspace_activity():
    from contextlib import asynccontextmanager
    from unittest.mock import Mock

    from src.platform.scope_sandbox.workbench_service import WorkbenchService

    @asynccontextmanager
    async def borrow(binding):
        yield "channel"

    binding = {"id": "workspace", "generation": "generation", "compute_epoch": 1}
    service = SimpleNamespace(
        bound=AsyncMock(return_value=(binding, {"revision": 1})),
        repository=SimpleNamespace(
            activity=Mock(side_effect=AssertionError("watch is not activity"))
        ),
        billing=SimpleNamespace(check_session=AsyncMock()),
        channels=SimpleNamespace(borrow=borrow),
    )
    async with WorkbenchService.files(service, "user", "project", "generation", watch=True):
        pass
    service.repository.activity.assert_not_called()
