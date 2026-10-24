"""Byte-bounded object ingestion, independent of Git transport and S3 layout.

The calling thread produces batches only when a worker slot and byte credit
are available, so at most one batch is held as lookahead; oversized objects
run alone. Neither queued work nor buffered payload grows with the input.
Batch writes run on the storage I/O pool with the caller's context and are
joined on every exit. The caller keeps heartbeats (``progress``) and
cooperative cancellation (``checkpoint``) running at least once a second.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass

from src.infra.execution.context import checkpoint
from src.version_engine.storage import io_pool
from src.version_engine.storage.io_strategy import IOStorageStrategy
from src.version_engine.write_engine.trace import trace_mark, trace_phase


@dataclass(frozen=True)
class ObjectBatch:
    objects: dict[str, bytes]
    size_bytes: int


def object_batches(
    objects: Iterable[tuple[str, bytes]],
    strategy: IOStorageStrategy,
) -> Iterator[ObjectBatch]:
    """Partition encoded bytes; the count ceiling only protects metadata RAM.

    One object may exceed the target but never the explicit object budget.
    Its physical subdivision belongs to the storage layout, not this iterator.
    """
    batch: dict[str, bytes] = {}
    size = 0
    for oid, data in objects:
        if not isinstance(data, bytes) or not data:
            raise ValueError("object stream requires nonempty encoded bytes")
        if len(data) > strategy.max_stream_object_bytes:
            raise ValueError("encoded object exceeds stream byte budget")
        if oid in batch:
            if batch[oid] != data:
                raise ValueError("conflicting bytes for one object identity")
            continue
        if batch and (
            size + len(data) > strategy.bundle_target_bytes
            or len(batch) >= strategy.max_batch_objects
        ):
            yield ObjectBatch(batch, size)
            batch, size = {}, 0
        batch[oid] = data
        size += len(data)
        if size >= strategy.bundle_target_bytes:
            yield ObjectBatch(batch, size)
            batch, size = {}, 0
    if batch:
        yield ObjectBatch(batch, size)


def write_object_stream(
    objects: Iterable[tuple[str, bytes]],
    strategy: IOStorageStrategy,
    write_batch: Callable[[dict[str, bytes]], None],
    *,
    progress: Callable[[], None] | None = None,
) -> None:
    """Bound the working set and retain I/O ownership on failure/cancellation."""
    batches = object_batches(objects, strategy)
    pending: dict[Future, int] = {}
    inflight_bytes = 0
    batch_count = object_count = total_bytes = peak_bytes = peak_batches = 0

    def reap():
        nonlocal inflight_bytes
        error = None
        for future in [future for future in pending if future.done()]:
            inflight_bytes -= pending.pop(future)
            error = error or future.exception()
        if error is not None:
            raise error

    def wait_for_credit():
        with trace_phase("storage.stream.backpressure", inflight_bytes=inflight_bytes):
            wait(pending, timeout=1, return_when=FIRST_COMPLETED)
        checkpoint()
        if progress is not None:
            progress()
        reap()

    try:
        while True:
            reap()
            checkpoint()
            while len(pending) >= strategy.write_concurrency:
                wait_for_credit()
            # Encoding can be CPU intensive. Only this single producer touches
            # the incoming spool; it never runs concurrently with itself.
            batch = next(batches, None)
            if batch is None:
                break
            while pending and inflight_bytes + batch.size_bytes > strategy.max_inflight_bytes:
                wait_for_credit()
            reap()  # A worker may have failed while the producer was encoding.
            if progress is not None:
                progress()
            future = io_pool.submit(write_batch, batch.objects)
            pending[future] = batch.size_bytes
            inflight_bytes += batch.size_bytes
            batch_count += 1
            object_count += len(batch.objects)
            total_bytes += batch.size_bytes
            peak_bytes = max(peak_bytes, inflight_bytes)
            peak_batches = max(peak_batches, len(pending))
            del batch
        while pending:
            wait_for_credit()
    finally:
        try:
            # Never cancel started workers: a DB or SDK call may already have
            # performed its side effect. Each finishes its admission path.
            io_pool.join(pending)
        finally:
            batches.close()
            trace_mark(
                "storage.stream.summary",
                batches=batch_count,
                objects=object_count,
                bytes=total_bytes,
                peak_inflight_bytes=peak_bytes,
                peak_inflight_batches=peak_batches,
            )
