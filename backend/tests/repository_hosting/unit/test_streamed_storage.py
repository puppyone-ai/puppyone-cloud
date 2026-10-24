"""Real storage pipeline with substituted S3/PG; no hosted performance claims."""

import contextvars
import threading
import time
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.infra.execution.context import current_execution
from src.version_engine.domain.errors import StorageWriteError
from src.version_engine.storage.backends.s3 import CachedStorageBackend, S3StorageBackend
from src.version_engine.storage.io_strategy import IOStorageStrategy
from src.version_engine.storage.mutation_context import publication_context, publication_storage
from src.version_engine.write_engine.git_object_format import encode_object
from tests.version_engine.test_l6_io_storage_strategy import _FakeS3, _FakeSupabase

pytestmark = pytest.mark.hosting_component


class FencedDB(_FakeSupabase):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.reservations = {}
        self.settled = set()

    def rpc(self, name, params):
        def execute():
            assert publication_context.get().pin_id == "pin"
            assert params["p_project_id"] == "proj" and params["p_actor"] == "actor"
            assert params["p_pin_id"] == "pin"
            self.calls.append(name)
            if name in {"reserve_version_object_capacity", "reserve_version_object_capacity_page"}:
                rows = params["p_objects"]
                assert len(rows) <= (16384 if name.endswith("_page") else 200) and params["p_required"]
                self.reservations.setdefault(params["p_io_id"], set()).update(
                    r["object_id"] for r in rows
                )
                return SimpleNamespace(
                    data=dict(
                        new_objects=len(rows), new_body_bytes=sum(r["body_bytes"] for r in rows)
                    )
                )
            if name in {"register_version_object_locations", "register_version_object_location_page"}:
                assert len(params["p_rows"]) <= (16384 if name.endswith("_page") else 200)
                for row in params["p_rows"]:
                    self.rows[(row["project_id"], row["object_id"])] = row
                return SimpleNamespace(data={"registered": len(params["p_rows"])})
            assert name == "settle_version_object_capacity_io"
            objects = self.reservations[params["p_io_id"]]
            assert all(("proj", oid) in self.rows for oid in objects)
            self.settled.add(params["p_io_id"])
            return SimpleNamespace(data=dict(settled_objects=len(objects)))

        return SimpleNamespace(execute=execute)


class FencedS3(_FakeS3):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.active = self.peak = 0
        self._lock = threading.Lock()  # uploads run on storage pool threads

    def for_single_attempt_io(self):
        return self

    def upload_file(self, key, content, **kwargs):
        assert self.db.reservations  # admission precedes all physical writes
        assert publication_context.get().pin_id == "pin"
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.002)
            return super().upload_file(key, content, **kwargs)
        finally:
            with self._lock:
                self.active -= 1


def test_many_tiny_objects_use_one_bundle_and_keep_fenced_metadata_pages():
    db = FencedDB()
    s3 = FencedS3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    objects = dict(encode_object("blob", f"object-{n}".encode()) for n in range(1001))
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        backend.put_stream(iter(objects.items()))
    assert len(s3.uploads) == len(s3.download_file_keys) == 1
    assert len(db.reservations) == len(db.settled) == 1
    assert Counter(db.calls) == {
        "reserve_version_object_capacity": 6,
        "register_version_object_locations": 6,
        "settle_version_object_capacity_io": 1,
    }
    # Fresh reader and the existing location format; no writer cache is proof.
    cold = S3StorageBackend(s3, "proj", supabase=db)
    for oid, data in objects.items():
        assert cold.get(oid) == data


def test_concurrent_byte_windows_keep_separate_io_identities_and_roundtrip():
    db = FencedDB()
    s3 = FencedS3(db)
    strategy = replace(IOStorageStrategy(), bundle_target_bytes=1024, chunk_bytes=512)
    backend = S3StorageBackend(s3, "proj", supabase=db, io_strategy=strategy)
    objects = dict(encode_object("blob", f"object-{n}".encode()) for n in range(500))
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        backend.put_stream(iter(objects.items()))
    assert 1 < s3.peak <= strategy.write_concurrency
    assert len(db.reservations) > 1 and db.settled == set(db.reservations)
    assert set.union(*db.reservations.values()) == set(objects)
    assert s3.active == 0
    cold = S3StorageBackend(s3, "proj", supabase=db)
    for oid, data in objects.items():
        assert cold.get(oid) == data


def test_failed_readback_never_registers_or_settles_the_batch():
    db = FencedDB()

    class CorruptS3(FencedS3):
        def download_file(self, key):
            return (super().download_file(key)) + b"corruption"

    s3 = CorruptS3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    with (
        publication_storage("proj", "actor", "pin", require_capacity=True),
        pytest.raises(StorageWriteError, match="verification failed"),
    ):
        backend.put_stream([encode_object("blob", b"body")])
    assert not db.rows and not db.settled and s3.active == 0


def run_storage_call(function, execution=None):
    """Run a storage call on its own thread (copied context, optional Execution)."""
    outcome = {}

    def target():
        token = current_execution.set(execution) if execution is not None else None
        try:
            outcome["result"] = function()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            if token is not None:
                current_execution.reset(token)

    thread = threading.Thread(target=contextvars.copy_context().run, args=(target,), daemon=True)
    thread.start()
    return thread, outcome


def test_readback_failure_joins_other_chunk_reads_before_releasing_owner():
    started, release = threading.Event(), threading.Event()
    finished = []

    class S3:
        def download_file(self, key):
            if key == "bad":
                assert started.wait(2)
                return b"bad"
            started.set()
            assert release.wait(3)
            finished.append(key)
            return b"expected"

    backend = S3StorageBackend(S3(), "proj")
    thread, outcome = run_storage_call(
        lambda: backend._verify_physical_uploads(
            [
                ("bad", b"expected", "binary"),
                ("slow", b"expected", "binary"),
            ]
        )
    )
    assert started.wait(2)
    time.sleep(0.02)
    assert thread.is_alive()  # the failed reader's owner waits for the slow one
    release.set()
    thread.join(5)
    assert isinstance(outcome.get("error"), StorageWriteError)
    assert finished == ["slow"]


def test_cached_backend_delegates_stream_without_materializing_or_caching_it():
    seen = []
    source = iter([encode_object("blob", b"value")])

    class Inner:
        def put_stream_durable(self, objects):
            assert objects is source
            seen.extend(objects)

    backend = CachedStorageBackend(Inner())
    backend.put_stream_durable(source)
    assert len(seen) == 1
