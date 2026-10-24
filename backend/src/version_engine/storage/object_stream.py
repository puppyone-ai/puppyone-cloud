"""Byte-bounded object ingestion, independent of Git transport and S3 layout.

The producer is pulled only when a worker slot is available. At most one batch
is held as lookahead for byte admission. Oversized objects run alone; neither
the number of tasks nor queued payload grows with the length of the input.
Workers keep their caller's publication context and are joined on every exit.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass

from src.infra.execution.runner import run_blocking
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


async def join_owned(tasks):
    """Drain even after repeated cancellation, before releasing source/pins.

    Do not cancel workers: a DB or SDK thread may already have performed its
    side effect. Each worker must finish its existing admission/settlement path.
    Exceptions are retrieved so none become detached background failures.
    """
    joined = asyncio.gather(*tasks, return_exceptions=True)
    cancelled = False
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            cancelled = True
    results = joined.result()
    if cancelled:
        raise asyncio.CancelledError
    return results


async def call_owned(function, *args):
    return await run_blocking(function, *args)


async def write_object_stream(
    objects: Iterable[tuple[str, bytes]],
    strategy: IOStorageStrategy,
    write_batch: Callable[[dict[str, bytes]], Awaitable[None]],
    *,
    progress: Callable[[], None] | None = None,
) -> None:
    """Bound the working set and retain I/O ownership on failure/cancellation."""
    batches = object_batches(objects, strategy)
    pending: dict[asyncio.Task, int] = {}
    inflight_bytes = 0
    batch_count = object_count = total_bytes = peak_bytes = peak_batches = 0

    def reap():
        nonlocal inflight_bytes
        done = [task for task in pending if task.done()]
        error = None
        for task in done:
            inflight_bytes -= pending.pop(task)
            try:
                task.result()
            except BaseException as exc:
                error = error or exc
        if error is not None:
            raise error

    async def wait_for_credit():
        with trace_phase("storage.stream.backpressure", inflight_bytes=inflight_bytes):
            await asyncio.wait(pending, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        if progress is not None:
            await call_owned(progress)
        reap()

    try:
        while True:
            reap()
            while len(pending) >= strategy.write_concurrency:
                await wait_for_credit()
            # Encoding can be CPU intensive. Only this single producer touches
            # the incoming spool; it never runs concurrently with itself.
            batch = await call_owned(next, batches, None)
            if batch is None:
                break
            while pending and inflight_bytes + batch.size_bytes > strategy.max_inflight_bytes:
                await wait_for_credit()
            reap()  # A worker may have failed while the producer was encoding.
            if progress is not None:
                await call_owned(progress)
            task = asyncio.create_task(write_batch(batch.objects))
            pending[task] = batch.size_bytes
            inflight_bytes += batch.size_bytes
            batch_count += 1
            object_count += len(batch.objects)
            total_bytes += batch.size_bytes
            peak_bytes = max(peak_bytes, inflight_bytes)
            peak_batches = max(peak_batches, len(pending))
            del batch
        while pending:
            await wait_for_credit()
    finally:
        try:
            await join_owned(pending)
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
