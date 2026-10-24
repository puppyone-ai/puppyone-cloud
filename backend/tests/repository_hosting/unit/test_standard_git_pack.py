"""Native pack storage interoperability, durability and bounded transfer faults."""

import io
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.infra.execution.context import Execution
from src.version_engine.domain.errors import StorageWriteError
from src.version_engine.storage import git_pack_io, git_process
from src.version_engine.storage.backends.s3 import S3StorageBackend
from src.version_engine.storage.git_pack import GitPack
from src.version_engine.storage.git_pack_cache import pack_cache
from src.version_engine.storage.git_pack_limits import PackLimits
from src.version_engine.storage.io_budget import operation_io_budget
from src.version_engine.storage.mutation_context import publication_context, publication_storage
from src.version_engine.storage.object_store import FileSystemBackend
from src.version_engine.storage.publication import ClosureVerifier
from src.version_engine.write_engine.git_object_format import decode_object
from tests.repository_hosting.harness.git import Git
from tests.repository_hosting.unit.test_streamed_storage import FencedDB, FencedS3, run_storage_call

pytestmark = pytest.mark.hosting_component


class StreamS3(FencedS3):
    def download_file_stream(self, key, chunk_size=8192):
        data = self.download_file(key)
        for offset in range(0, len(data), chunk_size):
            yield data[offset : offset + chunk_size]


@pytest.fixture
def packed(tmp_path, request):
    fmt = getattr(request, "param", "sha1")
    git = Git.init(tmp_path / "source", format=fmt)
    git.commit({"file": b"repeated contents\n" * 10000})
    git.commit({"file": b"repeated contents\n" * 9999 + b"changed\n"})
    payload = git.run("pack-objects", "--stdout", "--all", "--delta-base-offset").stdout
    with GitPack(fmt) as pack:
        pack.read(io.BytesIO(payload))
        yield git, payload, pack
    pack_cache.clear()


@pytest.mark.parametrize("packed", ["sha1", "sha256"], indirect=True)
def test_s3_stores_original_pack_and_cold_reader_preserves_objects(packed, monkeypatch):
    git, payload, pack = packed
    db = FencedDB()
    s3 = StreamS3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        # Received objects must never visit the loose encoder or POB builder.
        with monkeypatch.context() as patch:
            patch.setattr(
                "src.version_engine.storage.git_pack.encode_object",
                Mock(side_effect=AssertionError("re-encoded incoming object")),
            )
            patch.setattr(
                backend,
                "put_prepared_stream_durable",
                Mock(side_effect=AssertionError("converted incoming pack")),
            )
            backend.put_git_pack_durable(pack)
        assert len(db.reservations) == len(db.settled) == 1
        assert len(s3.uploads) == len(s3.download_file_keys) == 1
        key, stored = next(iter(s3.uploads.items()))
        assert key.endswith(".pack") and stored == payload
        assert all(row["pack_key"] == key for row in db.rows.values())
        assert set(publication_context.get().readbacks.records) == set(pack.objects)
        verifier = ClosureVerifier(backend, object_format=pack.format)
        verifier.proofs = SimpleNamespace(prefetch=Mock(), get=Mock(return_value=None))
        verifier.verify({git.text("rev-parse", "HEAD"): "commit"})
        assert (
            len(s3.download_file_keys) == 1
        )  # Physical receipt reused only in its own publication.
    pack.close()  # No live incoming repo can conceal a lost durable pack.
    cold = S3StorageBackend(s3, "proj", supabase=db)
    for oid, expected in git.objects().items():
        assert decode_object(cold.get(oid)) == expected
    assert len(s3.download_file_keys) == 2  # One bounded cold pack fill, indexed reads afterwards.
    # A new durability proof cannot use yesterday's cache as evidence.
    s3.uploads[key] = stored[:-1] + bytes([stored[-1] ^ 1])
    with pytest.raises((ValueError, StorageWriteError), match=r"pack|checksum"):
        cold.durable_readback().get_durable(git.text("rev-parse", "HEAD"))


@pytest.mark.parametrize("failure", ["reserve", "upload", "readback", "index", "settle"])
def test_failed_pack_never_promotes_publishable_evidence(packed, failure):
    _, _, pack = packed

    class DB(FencedDB):
        def rpc(self, name, params):
            if (
                (failure == "reserve" and name.startswith("reserve"))
                or (failure == "index" and name.startswith("register"))
                or (failure == "settle" and name.startswith("settle"))
            ):
                return SimpleNamespace(execute=Mock(side_effect=RuntimeError("injected failure")))
            return super().rpc(name, params)

    class S3(StreamS3):
        def upload_file(self, *args, **kwargs):
            if failure == "upload":
                raise ConnectionError("injected failure")
            return super().upload_file(*args, **kwargs)

        def download_file(self, key):
            data = super().download_file(key)
            return data + b"corrupt" if failure == "readback" else data

    db = DB()
    s3 = S3(db)
    backend = S3StorageBackend(s3, "proj", supabase=db)
    with publication_storage("proj", "actor", "pin", require_capacity=True):
        with pytest.raises((ValueError, StorageWriteError, ConnectionError, RuntimeError)):
            backend.put_git_pack_durable(pack)
        assert not publication_context.get().readbacks.records
    assert not db.settled
    if failure == "reserve":
        assert not s3.uploads
    if failure in {"reserve", "upload", "readback", "index"}:
        assert not db.rows


def test_filesystem_pack_keeps_delta_bases_until_all_members_deleted(packed, tmp_path):
    git, payload, pack = packed
    backend = FileSystemBackend(tmp_path / "durable")
    backend.put_git_pack_durable(pack)
    assert backend.count() == (len(pack.objects), len(payload))
    pack.close()
    objects = git.objects()
    for oid in objects:
        assert decode_object(backend.get(oid)) == objects[oid]
        assert backend.delete(oid)
        assert not backend.exists(oid)
        assert not backend.delete(oid)
    assert backend.count() == (0, 0)
    assert not list((backend.dir / "pack").glob("*.pack"))


@pytest.mark.parametrize("fault", [None, "part", "complete"])
def test_multipart_bounds_memory_concurrency_and_joins_before_abort(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(git_pack_io, "PART_BYTES", 32)
    path = tmp_path / "pack"
    path.write_bytes(bytes(range(255)) * 3)

    class S3:
        active = peak = 0
        complete = aborted = False

        def __init__(self):
            self.parts = {}

        def create_multipart_upload(self, *args):
            return "upload"

        lock = threading.Lock()

        def upload_part(self, key, upload, number, data):
            assert len(data) <= 32
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep(0.001)
                if fault == "part" and number == 2:
                    raise ConnectionError("part failed")
                self.parts[number] = data
                return str(number)
            finally:
                with self.lock:
                    self.active -= 1

        def complete_multipart_upload(self, key, upload, parts):
            assert not self.active
            assert [n for n, _ in parts] == sorted(self.parts)
            if fault == "complete":
                raise ConnectionError("completion response lost")
            self.complete = True

        def abort_multipart_upload(self, *args):
            assert not self.active
            self.aborted = True

    s3 = S3()

    def run():
        with operation_io_budget(2):
            git_pack_io.upload_pack(s3, "pack", path)

    if fault:
        with pytest.raises(ConnectionError):
            run()
        assert s3.aborted and not s3.complete
    else:
        run()
        assert s3.complete and not s3.aborted
        # Parts complete in any order; their numbers define the object.
        assert b"".join(s3.parts[n] for n in sorted(s3.parts)) == path.read_bytes()
    assert s3.peak == 2 and not s3.active


def test_pack_limits_clean_up_and_valid_retry_succeeds(packed):
    _, payload, _ = packed
    with GitPack("sha1", limits=PackLimits(receive_bytes=len(payload) - 1)) as rejected:
        directory = rejected.directory
        with pytest.raises(ValueError, match="byte limit"):
            rejected.read(io.BytesIO(payload))
    assert not directory.exists()
    with GitPack("sha1") as retry:
        retry.read(io.BytesIO(payload))
        assert retry.path.read_bytes() == payload


def test_large_expanded_repository_keeps_only_compressed_pack_on_disk(tmp_path):
    """576 MiB of logical bodies must not become a cumulative raw spool."""
    git = Git.init(tmp_path / "large")
    entries = []
    for i in range(18):
        oid = git.run(
            "hash-object", "-w", "--stdin", input=bytes([65 + i]) * (32 * 1024**2)
        ).stdout.strip()
        entries.append(b"100644 blob " + oid + b"\tfile-" + str(i).encode() + b"\n")
    tree = git.run("mktree", input=b"".join(entries)).stdout.strip()
    commit = git.run("commit-tree", tree.decode(), "-m", "large compressed fixture").stdout.strip()
    payload = git.run(
        "pack-objects", "--stdout", "--revs", "--window=0", input=commit + b"\n"
    ).stdout
    assert len(payload) < 1024**2
    with GitPack("sha1") as pack:
        pack.read(io.BytesIO(payload))
        assert sum(row[2] for row in pack.objects.values()) > 512 * 1024**2
        assert pack.path.read_bytes() == payload
        assert sum(p.stat().st_size for p in pack.directory.rglob("*") if p.is_file()) < 2 * 1024**2
        assert not list((pack.directory / "objects").glob("??/*"))
        backend = FileSystemBackend(tmp_path / "durable-large")
        backend.put_git_pack_durable(pack)
    from src.platform.access.adapters.git.object_reader import PublishedObjectReader

    root = commit.decode()
    snapshot = SimpleNamespace(
        object_format="sha1",
        roots={root: "commit"},
        backend=backend.durable_readback(),
        check_live=lambda: None,
    )
    reader = PublishedObjectReader(snapshot, None)
    reader.get(root)
    reader.get(tree.decode())
    for row in entries:
        oid = row.split()[2].decode()
        kind, body = reader.get(oid)
        assert kind == "blob" and len(body) == 32 * 1024**2
    assert reader.total_bytes > 512 * 1024**2
    pack_cache.clear()


def test_interrupted_object_read_reaps_child_and_next_read_reopens(packed, monkeypatch):
    git, _, pack = packed
    head = git.text("rev-parse", "HEAD")
    expected = ("commit", git.run("cat-file", "commit", head).stdout)
    assert pack.get(head) == expected
    old = pack._reader
    with monkeypatch.context() as patch:
        patch.setattr(git_process, "read_pipe", Mock(side_effect=InterruptedError("cancelled")))
        with pytest.raises(InterruptedError):
            pack.get(head)
    assert old.poll() is not None and pack._reader is None
    assert pack.get(head) == expected


def test_cancellation_joins_started_parts_before_abort(tmp_path, monkeypatch):
    monkeypatch.setattr(git_pack_io, "PART_BYTES", 32)
    path = tmp_path / "cancel.pack"
    path.write_bytes(b"x" * 100)
    started, release = threading.Event(), threading.Event()
    active = 0
    aborted = False

    class S3:
        def create_multipart_upload(self, *_):
            return "upload"

        lock = threading.Lock()

        def upload_part(self, *_):
            nonlocal active
            with self.lock:
                active += 1
            started.set()
            try:
                assert release.wait(3)
                return "etag"
            finally:
                with self.lock:
                    active -= 1

        def abort_multipart_upload(self, *_):
            nonlocal aborted
            assert active == 0
            aborted = True

    execution = Execution()
    thread, outcome = run_storage_call(lambda: git_pack_io.upload_pack(S3(), "key", path), execution)
    assert started.wait(2)
    execution.cancelled.set()  # cooperative; started part uploads cannot be killed
    time.sleep(0.02)
    assert thread.is_alive() and not aborted
    release.set()
    thread.join(5)
    assert isinstance(outcome.get("error"), RuntimeError)
    assert aborted and active == 0
