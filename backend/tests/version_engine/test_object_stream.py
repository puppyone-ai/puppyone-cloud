"""Ingestion bounds and ownership tested with blocked, failed and cancelled I/O."""

import contextvars
import threading
import time
from dataclasses import replace

import pytest

from src.infra.execution.context import Execution, checkpoint, current_execution
from src.version_engine.storage.io_budget import operation_io_budget, physical_io_credit
from src.version_engine.storage.io_pool import parallel_each
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


def _run_in_thread(function):
    """Run a storage call on its own thread; return (thread, outcome dict)."""
    outcome = {}

    def target():
        try:
            outcome["result"] = function()
        except BaseException as exc:
            outcome["error"] = exc

    # Like a task, the storage call runs in a copy of the caller's context.
    thread = threading.Thread(target=contextvars.copy_context().run, args=(target,), daemon=True)
    thread.start()
    return thread, outcome


def test_backpressure_stops_producer_and_preserves_publication_context():
    release, full = threading.Event(), threading.Event()
    produced, running, completed = [], set(), []
    lock = threading.Lock()

    def source():
        assert publication_context.get().pin_id == "pin"
        for n in range(30):
            produced.append(n)
            yield str(n), b"x" * 10

    def write(batch):
        assert publication_context.get().pin_id == "pin"  # copied into the worker
        with lock:
            running.update(batch)
            if len(running) == 2:
                full.set()
        assert release.wait(3)
        with lock:
            running.difference_update(batch)
            completed.extend(batch)

    with publication_storage("project", "actor", "pin"):
        thread, outcome = _run_in_thread(
            lambda: write_object_stream(source(), policy(write_concurrency=2), write)
        )
        assert full.wait(2)
        assert produced == [0, 1]  # no eager input list or queued writes
        release.set()
        thread.join(5)
    assert "error" not in outcome
    assert len(completed) == 30 and not running


def test_byte_credit_runs_an_oversized_object_exclusively():
    running_bytes = 0
    seen = []
    lock = threading.Lock()

    def write(batch):
        nonlocal running_bytes
        size = sum(map(len, batch.values()))
        with lock:
            running_bytes += size
            seen.append(running_bytes)
            assert running_bytes <= 20 or (size == 25 and running_bytes == 25)
        time.sleep(0.005)
        with lock:
            running_bytes -= size

    source = ((str(n), b"x" * size) for n, size in enumerate([10, 10, 25, 10, 10]))
    write_object_stream(source, policy(), write)
    assert 25 in seen and running_bytes == 0


@pytest.mark.parametrize("exit_kind", ["failure", "cancel", "producer"])
def test_every_exit_joins_started_workers_before_return(exit_kind):
    release, started = threading.Event(), threading.Event()
    finished = []
    execution = Execution()

    def source():
        yield "slow", b"x" * 10
        if exit_kind == "producer":
            raise ValueError("producer failed")
        yield "bad", b"x" * 10

    def write(batch):
        if "bad" in batch and exit_kind == "failure":
            assert started.wait(2)
            raise ValueError("upload failed")
        started.set()
        assert release.wait(3)
        finished.extend(batch)

    def run():
        token = current_execution.set(execution)
        try:
            write_object_stream(source(), policy(write_concurrency=2), write)
        finally:
            current_execution.reset(token)

    thread, outcome = _run_in_thread(run)
    assert started.wait(2)
    if exit_kind == "cancel":
        execution.cancelled.set()  # cooperative: a started worker cannot be killed
    time.sleep(0.03)
    assert thread.is_alive()
    release.set()
    thread.join(5)
    expected = RuntimeError if exit_kind == "cancel" else ValueError
    assert isinstance(outcome.get("error"), expected)
    assert "slow" in finished


def test_cancel_waits_for_the_producer_before_closing_its_spool():
    release, started = threading.Event(), threading.Event()
    finished = threading.Event()
    execution = Execution()

    def source():
        started.set()
        release.wait(3)
        finished.set()
        yield "object", b"x"

    def forbidden(_batch):
        pytest.fail("cancelled producer scheduled a new upload")

    def run():
        token = current_execution.set(execution)
        try:
            write_object_stream(source(), policy(), forbidden, progress=checkpoint)
        finally:
            current_execution.reset(token)

    thread, outcome = _run_in_thread(run)
    assert started.wait(2)
    execution.cancelled.set()
    time.sleep(0.02)
    assert thread.is_alive()
    release.set()
    thread.join(5)
    assert isinstance(outcome.get("error"), RuntimeError)
    assert finished.is_set()


def test_physical_credits_bound_nested_fanout_across_operations():
    active = peak = 0
    local = [0] * 4
    lock = threading.Lock()

    def request(operation):
        nonlocal active, peak
        with physical_io_credit():
            with lock:
                active += 1
                local[operation] += 1
                peak = max(peak, active)
                assert local[operation] <= 3 and active <= 8
            time.sleep(0.005)
            with lock:
                local[operation] -= 1
                active -= 1

    def operation(n):
        with operation_io_budget(3):
            parallel_each(lambda _: request(n), range(20), concurrency=20)

    threads = [threading.Thread(target=operation, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert peak == 8 and active == 0


def test_nested_parallel_fanout_never_waits_for_unstarted_helpers():
    # Saturate the pool with outer items whose own fan-out must still finish.
    outer = range(32)
    results = parallel_each(
        lambda n: sum(parallel_each(lambda m: m, range(10), concurrency=10)) + n,
        outer,
        concurrency=32,
    )
    assert results == [45 + n for n in outer]
