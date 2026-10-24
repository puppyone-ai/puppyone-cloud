"""Operation ownership and byte page bounds; substituted PG/S3, not latency."""

import asyncio
import threading
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.version_engine.domain.errors import StorageWriteError
from src.version_engine.storage.backends.s3 import S3StorageBackend
from src.version_engine.storage.io_strategy import IOStorageStrategy
from src.version_engine.storage.metadata_pages import metadata_pages
from src.version_engine.storage.mutation_context import publication_storage
from src.version_engine.write_engine.git_object_format import decode_object, encode_object
from tests.repository_hosting.unit.test_streamed_storage import FencedDB, FencedS3

pytestmark = pytest.mark.hosting_component


def fixture(count=1001):
    objects = dict(encode_object("blob", f"object-{n}".encode()) for n in range(count))
    facts = {oid: ("blob", len(decode_object(data)[1])) for oid, data in objects.items()}
    return objects, facts


@pytest.mark.asyncio
async def test_many_physical_windows_share_one_capacity_and_location_page_and_settlement():
    db = FencedDB()
    s3 = FencedS3(db)
    backend = S3StorageBackend(
        s3, "proj", supabase=db, io_strategy=replace(IOStorageStrategy(), bundle_target_bytes=1024)
    )
    objects, facts = fixture()
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        await backend.async_put_prepared_stream(iter(objects.items()), facts)
    assert len(s3.uploads) > 1 and 1 < s3.peak <= 4
    assert len(db.reservations) == len(db.settled) == 1
    assert Counter(db.calls) == {
        "reserve_version_object_capacity_page": 1,
        "register_version_object_location_page": 1,
        "settle_version_object_capacity_io": 1,
    }
    assert db.calls[-1] == "settle_version_object_capacity_io"
    cold = S3StorageBackend(s3, "proj", supabase=db)
    for oid, data in objects.items():
        assert await cold.async_get(oid) == data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["denied", "readback", "missing", "extra", "wrong-fact", "index"]
)
async def test_partial_or_unverified_ingestion_cannot_settle(failure):
    class DB(FencedDB):
        def rpc(self, name, params):
            if (failure == "denied" and name.startswith("reserve")) or (
                failure == "index" and name.startswith("register")
            ):

                def execute():
                    raise RuntimeError("rejected")

                return SimpleNamespace(execute=execute)
            return super().rpc(name, params)

    class S3(FencedS3):
        def download_file(self, key):
            data = super().download_file(key)
            return data + b"broken" if failure == "readback" else data

    db = DB()
    s3 = S3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    objects, facts = fixture(10)
    first = next(iter(objects))
    if failure == "missing":
        objects.pop(first)
    if failure == "extra":
        facts.pop(first)
    if failure == "wrong-fact":
        facts[first] = ("blob", 999)
    with (
        publication_storage("proj", "actor", "pin", require_capacity=True),
        pytest.raises((ValueError, StorageWriteError)),
    ):
        await backend.async_put_prepared_stream(objects.items(), facts)
    assert not db.settled and s3.active == 0
    if failure in {"denied", "extra", "wrong-fact"}:
        assert not s3.uploads
    if failure == "readback":
        assert not db.rows


@pytest.mark.asyncio
async def test_cancel_joins_started_io_and_keeps_operation_fenced():
    started, release = threading.Event(), threading.Event()

    class S3(FencedS3):
        def upload_file(self, *args, **kwargs):
            started.set()
            assert release.wait(3)
            return super().upload_file(*args, **kwargs)

    db = FencedDB()
    s3 = S3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    objects, facts = fixture()
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        task = asyncio.create_task(backend.async_put_prepared_stream(objects.items(), facts))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not db.settled
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not db.settled and s3.active == 0


def test_metadata_pages_enforce_bytes_including_json_overhead_and_entry_budget(monkeypatch):
    import json

    from src.version_engine.storage import metadata_pages as module

    monkeypatch.setattr(module, "MAX_METADATA_BYTES", 140)
    monkeypatch.setattr(module, "MAX_METADATA_ROWS", 3)
    rows = [{"object_id": str(n), "text": "文" * 4} for n in range(100)]
    pages = list(metadata_pages(iter(rows)))
    assert [row for page in pages for row in page] == rows
    assert all(len(json.dumps(page).encode()) <= 140 and len(page) <= 3 for page in pages)
    with pytest.raises(ValueError, match="byte budget"):
        list(metadata_pages([{"text": "x" * 140}]))
