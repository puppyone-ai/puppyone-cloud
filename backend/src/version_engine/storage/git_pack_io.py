"""Bounded physical standard-pack transfer; no per-object encoding."""

import hashlib
from contextlib import closing, suppress

from src.infra.execution.context import checkpoint
from src.version_engine.storage.io_budget import physical_io_credit
from src.version_engine.storage.io_pool import parallel_each

PART_BYTES = 8 * 1024**2
PART_CONCURRENCY = 4


def _request(method, *args, **kwargs):
    with physical_io_credit():
        return method(*args, **kwargs)


def upload_pack(s3, key, path, *, progress=None):
    size = path.stat().st_size
    if size <= PART_BYTES:
        with path.open("rb") as source:
            data = source.read(PART_BYTES + 1)
        _request(s3.upload_file, key, data, content_type="application/x-git-packed-objects")
        return
    upload = _request(s3.create_multipart_upload, key, "application/x-git-packed-objects")
    parts = []
    try:
        with path.open("rb") as source:
            number = 1
            while True:
                checkpoint()
                if progress is not None:
                    progress()
                batch = []
                for _ in range(PART_CONCURRENCY):
                    data = source.read(PART_BYTES)
                    if not data:
                        break
                    batch.append((number, data))
                    number += 1
                if not batch:
                    break
                # Every started part finishes before a failure propagates. The
                # source and publication reservation outlive all physical writes.
                etags = parallel_each(
                    lambda part: _request(s3.upload_part, key, upload, *part),
                    batch,
                    concurrency=PART_CONCURRENCY,
                )
                parts.extend((n, etag) for (n, _), etag in zip(batch, etags, strict=True))
        _request(s3.complete_multipart_upload, key, upload, parts)
    except BaseException:
        # Capacity I/O remains unsettled for the existing reconciler.
        with suppress(Exception):
            _request(s3.abort_multipart_upload, key, upload)
        raise


def verify_pack(s3, key, *, expected_size, expected_digest, progress=None):
    digest = hashlib.sha256()
    total = 0
    with (
        physical_io_credit(),
        closing(s3.download_file_stream(key, chunk_size=1024 * 1024)) as stream,
    ):
        for block in stream:
            checkpoint()
            if progress is not None:
                progress()
            total += len(block)
            if total > expected_size:
                raise ValueError("stored Git pack exceeds its verified size")
            digest.update(block)
    if total != expected_size or digest.hexdigest() != expected_digest:
        raise ValueError("stored Git pack checksum mismatch")


def download_pack(s3, key, destination, *, max_bytes):
    total = 0
    with (
        physical_io_credit(),
        closing(s3.download_file_stream(key, chunk_size=1024 * 1024)) as stream,
        destination.open("wb") as output,
    ):
        for block in stream:
            checkpoint()
            total += len(block)
            if total > max_bytes:
                raise ValueError("stored Git pack exceeds its resource budget")
            output.write(block)
