"""Bounded physical standard-pack transfer; no per-object encoding."""

import asyncio
import hashlib
from contextlib import aclosing, suppress

from src.version_engine.infrastructure.owned_work import checkpoint
from src.version_engine.storage.io_budget import physical_io_credit
from src.version_engine.storage.object_stream import call_owned, join_owned

PART_BYTES = 8 * 1024**2
PART_CONCURRENCY = 4


async def _request(method, *args, **kwargs):
    async def run():
        async with physical_io_credit():
            return await method(*args, **kwargs)

    result = (await join_owned([asyncio.create_task(run())]))[0]
    if isinstance(result, BaseException):
        raise result
    return result


async def upload_pack(s3, key, path, *, progress=None):
    size = path.stat().st_size
    if size <= PART_BYTES:
        with path.open("rb") as source:
            data = source.read(PART_BYTES + 1)
        await _request(s3.upload_file, key, data, content_type="application/x-git-packed-objects")
        return
    upload = await _request(s3.create_multipart_upload, key, "application/x-git-packed-objects")
    parts = []
    try:
        with path.open("rb") as source:
            number = 1
            while True:
                checkpoint()
                if progress is not None:
                    await call_owned(progress)
                batch = []
                for _ in range(PART_CONCURRENCY):
                    data = source.read(PART_BYTES)
                    if not data:
                        break
                    batch.append((number, data))
                    number += 1
                if not batch:
                    break
                # Join every started part even if one fails. The source and
                # publication reservation must outlive all physical writes.
                results = await join_owned(
                    [
                        asyncio.create_task(_request(s3.upload_part, key, upload, n, data))
                        for n, data in batch
                    ]
                )
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
                parts.extend((n, etag) for (n, _), etag in zip(batch, results, strict=True))
        await _request(s3.complete_multipart_upload, key, upload, parts)
    except BaseException:
        # Capacity I/O remains unsettled for the existing reconciler.
        with suppress(Exception):
            await _request(s3.abort_multipart_upload, key, upload)
        raise


async def verify_pack(s3, key, *, expected_size, expected_digest, progress=None):
    digest = hashlib.sha256()
    total = 0
    async with (
        physical_io_credit(),
        aclosing(s3.download_file_stream(key, chunk_size=1024 * 1024)) as stream,
    ):
        async for block in stream:
            checkpoint()
            if progress is not None:
                await call_owned(progress)
            total += len(block)
            if total > expected_size:
                raise ValueError("stored Git pack exceeds its verified size")
            digest.update(block)
    if total != expected_size or digest.hexdigest() != expected_digest:
        raise ValueError("stored Git pack checksum mismatch")


async def download_pack(s3, key, destination, *, max_bytes):
    total = 0
    async with (
        physical_io_credit(),
        aclosing(s3.download_file_stream(key, chunk_size=1024 * 1024)) as stream,
    ):
        with destination.open("wb") as output:
            async for block in stream:
                checkpoint()
                total += len(block)
                if total > max_bytes:
                    raise ValueError("stored Git pack exceeds its resource budget")
                output.write(block)
