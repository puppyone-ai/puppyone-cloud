"""One owned ingestion for an already parsed, bounded incoming Git pack.

Exact object facts admit capacity before any physical write. They are checked
against the encoded stream, not trusted as durability evidence. Only placements
returned after physical readback enter the metadata buffer. An incomplete or
ambiguous operation keeps its existing database I/O fence for recovery/GC.
"""

import asyncio
import uuid

from src.version_engine.storage.metadata_pages import MetadataPage
from src.version_engine.storage.object_stream import write_object_stream
from src.version_engine.write_engine.trace import trace_phase


async def write_prepared_stream(
    objects, facts, strategy, *, reserve, write, register, settle, verify, progress
):
    remaining = dict(facts)
    if not remaining:
        raise ValueError("prepared ingestion requires object facts")
    io_id = str(uuid.uuid4())
    await reserve(
        (
            dict(object_id=oid, object_kind=kind, body_bytes=size)
            for oid, (kind, size) in remaining.items()
        ),
        io_id=io_id,
        paged=True,
    )
    page, lock = MetadataPage(), asyncio.Lock()

    async def write_batch(batch):
        # No await while consuming identities: parallel workers cannot claim
        # the same fact. A missing/extra/changed object prevents settlement.
        for oid, data in batch.items():
            if remaining.pop(oid, None) != verify(oid, data):
                raise ValueError("prepared ingestion object facts mismatch")
        with trace_phase(
            "storage.stream.batch", objects=len(batch), bytes=sum(map(len, batch.values()))
        ):
            rows = await write(batch, register=False)
        async with lock:
            for row in rows:
                ready = page.add(row)
                if ready:
                    await register(ready, paged=True)

    await write_object_stream(objects, strategy, write_batch, progress=progress)
    if remaining:
        raise ValueError("prepared ingestion omitted admitted objects")
    await register(page.take(), paged=True)
    # write_object_stream has joined every worker. Never settle from finally:
    # exceptions and uncertain RPC outcomes must retain durable I/O ownership.
    await settle(io_id)
