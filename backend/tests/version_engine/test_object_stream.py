"""Ingestion bounds and ownership tested with blocked, failed and cancelled I/O."""

import asyncio
import threading
from dataclasses import replace

import pytest

from src.version_engine.storage.io_budget import operation_io_budget, physical_io_credit
from src.version_engine.storage.io_strategy import IOStorageStrategy
from src.version_engine.storage.mutation_context import publication_context, publication_storage
from src.version_engine.storage.object_stream import object_batches, write_object_stream


def policy(**kwargs):
    return replace(IOStorageStrategy(), bundle_target_bytes=10, max_inflight_bytes=20, **kwargs)


def test_batches_use_encoded_bytes_not_one_hundred_object_rounds():
    # Thousands of tiny objects remain one physical payload window.
    batches = list(object_batches(((str(n), b"x") for n in range(6013)), IOStorageStrategy()))
    assert len(batches) == 1 and batches[0].size_bytes == 6013


@pytest.mark.parametrize(
    "sizes,expected",
    [
        ([], []),
        ([3, 7, 1], [10, 1]),
        ([6, 5, 4], [6, 9]),
        ([3, 25, 2], [3, 25, 2]),
        ([10, 10], [10, 10]),
    ],
)
def test_byte_boundaries_tail_and_oversized_isolation(sizes, expected):
    batches = list(
        object_batches(((str(n), b"x" * size) for n, size in enumerate(sizes)), policy())
    )
    assert [batch.size_bytes for batch in batches] == expected
    assert sum(len(batch.objects) for batch in batches) == len(sizes)


def test_metadata_guard_and_single_object_budget_are_separate():
    strategy = policy(max_batch_objects=2, max_stream_object_bytes=25)
    assert [
        len(b.objects) for b in object_batches(((str(i), b"x") for i in range(5)), strategy)
    ] == [2, 2, 1]
    with pytest.raises(ValueError, match="byte budget"):
        list(object_batches([("large", b"x" * 26)], strategy))
    with pytest.raises(ValueError, match="conflicting bytes"):
        list(object_batches([("same", b"a"), ("same", b"b")], strategy))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bundle_target_bytes": True},
        {"chunk_bytes": 1.5},
        {"write_concurrency": 0},
        {"max_inflight_bytes": 1},
        {"max_batch_objects": -1},
        {"max_stream_object_bytes": False},
    ],
)
def test_invalid_resource_profiles_fail_before_any_work(kwargs):
    with pytest.raises(ValueError):
        replace(IOStorageStrategy(), **kwargs)


@pytest.mark.asyncio
async def test_backpressure_stops_producer_and_preserves_publication_context():
    release, full = asyncio.Event(), asyncio.Event()
    produced, running, completed = [], set(), []

    def source():
        assert publication_context.get().pin_id == "pin"
        for n in range(30):
            produced.append(n)
            yield str(n), b"x" * 10

    async def write(batch):
        assert publication_context.get().pin_id == "pin"
        running.update(batch)
        if len(running) == 2:
            full.set()
        await release.wait()
        running.difference_update(batch)
        completed.extend(batch)

    with publication_storage("project", "actor", "pin"):
        task = asyncio.create_task(
            write_object_stream(source(), policy(write_concurrency=2), write)
        )
        await asyncio.wait_for(full.wait(), 2)
        assert produced == [0, 1]  # no eager input list or tasks waiting on a semaphore
        release.set()
        await task
    assert len(completed) == 30 and not running


@pytest.mark.asyncio
async def test_byte_credit_runs_an_oversized_object_exclusively():
    running_bytes = 0
    seen = []

    async def write(batch):
        nonlocal running_bytes
        size = sum(map(len, batch.values()))
        running_bytes += size
        seen.append(running_bytes)
        assert running_bytes <= 20 or (size == 25 and running_bytes == 25)
        await asyncio.sleep(0.005)
        running_bytes -= size

    source = ((str(n), b"x" * size) for n, size in enumerate([10, 10, 25, 10, 10]))
    await write_object_stream(source, policy(), write)
    assert 25 in seen and running_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["failure", "cancel", "producer"])
async def test_every_exit_joins_started_workers_before_return(exit_kind):
    release, started = asyncio.Event(), asyncio.Event()
    finished = []

    def source():
        yield "slow", b"x" * 10
        if exit_kind == "producer":
            raise ValueError("producer failed")
        yield "bad", b"x" * 10

    async def write(batch):
        if "bad" in batch and exit_kind == "failure":
            await started.wait()
            raise ValueError("upload failed")
        started.set()
        await release.wait()
        finished.extend(batch)

    task = asyncio.create_task(write_object_stream(source(), policy(write_concurrency=2), write))
    await asyncio.wait_for(started.wait(), 2)
    if exit_kind == "cancel":
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # cancellation during the drain must not detach workers
    await asyncio.sleep(0.03)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError if exit_kind == "cancel" else ValueError):
        await task
    assert "slow" in finished


@pytest.mark.asyncio
async def test_cancel_waits_for_the_producer_thread_before_closing_its_spool():
    release, started = threading.Event(), threading.Event()
    finished = threading.Event()

    def source():
        started.set()
        release.wait(3)
        finished.set()
        yield "object", b"x"

    async def forbidden(_batch):
        pytest.fail("cancelled producer scheduled a new upload")

    task = asyncio.create_task(write_object_stream(source(), policy(), forbidden))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_physical_credits_bound_nested_fanout_across_operations():
    active = peak = 0
    local = [0] * 4

    async def request(operation):
        nonlocal active, peak
        async with physical_io_credit():
            active += 1
            local[operation] += 1
            peak = max(peak, active)
            assert local[operation] <= 3 and active <= 8
            await asyncio.sleep(0.005)
            local[operation] -= 1
            active -= 1

    async def operation(n):
        with operation_io_budget(3):
            await asyncio.gather(*(request(n) for _ in range(20)))

    await asyncio.gather(*(operation(n) for n in range(4)))
    assert peak == 8 and active == 0
