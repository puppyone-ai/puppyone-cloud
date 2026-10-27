import asyncio
import threading
from contextlib import contextmanager

import pytest

from src.file_operations.async_queries import open_read_session
from tests.repository_hosting.integration.test_ref_transaction_service import grant


@pytest.mark.asyncio
async def test_async_queries_share_one_pin_and_close_on_error(native_reads):
    ops, _, _, calls, _, _, commit, *_ = native_reads
    with pytest.raises(ValueError, match="consumer failed"):
        async with open_read_session(ops.for_grant(grant("p")), "p") as session:
            for _ in range(35):
                assert await session.call(lambda read: read.read_file("p", "file")) == b"raw\x00bytes"
            assert await session.call(lambda read: read.get_head_commit_id("p")) == commit
            raise ValueError("consumer failed")
    assert calls.count("begin") == calls.count("release") == 1
    with pytest.raises(RuntimeError, match="closed"):
        await session.call(lambda read: read.stat("p", "file"))


@pytest.mark.asyncio
@pytest.mark.parametrize("during_open", [False, True])
async def test_cancel_joins_open_or_query_before_pin_release(native_reads, during_open):
    ops, _, _, calls, *_ = native_reads
    bound = ops.for_grant(grant("p"))
    entered, finish = threading.Event(), threading.Event()

    def block():
        entered.set()
        assert finish.wait(3)

    if during_open:
        original = bound.read_session

        @contextmanager
        def delayed(project):
            with original(project) as read:
                block()
                yield read

        bound.read_session = delayed

    async def consume():
        async with open_read_session(bound, "p") as session:
            await session.call(lambda read: (read.read_file("p", "file"), block()))

    task = asyncio.create_task(consume())
    try:
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert "release" not in calls
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls.count("begin") == calls.count("release") == 1
