"""L6 IO storage strategy and S3 backend routing contracts."""

from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest

from src.version_engine.storage.backends.s3 import S3StorageBackend
from src.version_engine.infrastructure.supabase.db_names import OBJECT_LOCATIONS_TABLE
from src.version_engine.storage.io_strategy import IOStorageStrategy, ObjectWriteLayout
from src.version_engine.storage.object_store import ObjectStore
from src.version_engine.write_engine.git_object_format import encode_object


def _strategy(
    *,
    bundle_target_bytes: int = 1024,
    chunk_bytes: int = 512,
    location_index_enabled: bool = True,
) -> IOStorageStrategy:
    return IOStorageStrategy(
        bundle_target_bytes=bundle_target_bytes,
        chunk_bytes=chunk_bytes,
        location_index_enabled=location_index_enabled,
    )


class _FakeS3:
    def __init__(self, *, max_upload_bytes: int | None = None):
        self.max_upload_bytes = max_upload_bytes
        self.uploads: dict[str, bytes] = {}
        self.upload_sizes: list[int] = []
        self.download_file_keys: list[str] = []
        self.download_range_keys: list[str] = []
        self.file_exists_keys: list[str] = []

    def upload_file(self, key, content, content_type=None, metadata=None):
        self.upload_sizes.append(len(content))
        if self.max_upload_bytes is not None and len(content) > self.max_upload_bytes:
            raise AssertionError(f"physical S3 object too large: {len(content)}")
        self.uploads[key] = content
        return SimpleNamespace(key=key)

    def download_file(self, key):
        self.download_file_keys.append(key)
        if key not in self.uploads:
            raise FileNotFoundError("not found")
        return self.uploads[key]

    def download_file_range(self, key, start=0, limit=None):
        self.download_range_keys.append(key)
        if key not in self.uploads:
            raise FileNotFoundError("not found")
        content = self.uploads[key]
        end = len(content) if limit is None else min(len(content), start + limit)
        return content[start:end], len(content)

    def file_exists(self, key):
        self.file_exists_keys.append(key)
        return key in self.uploads


class _FakeObjectLocationTable:
    def __init__(self, db):
        self.db = db
        self.filters = {}
        self._upsert_rows = None

    def upsert(self, rows, on_conflict=None):
        self._upsert_rows = rows
        return self

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, key, values):
        self.filters[key] = list(values)
        return self

    def limit(self, _value):
        return self

    def execute(self):
        if self._upsert_rows is not None:
            self.db.upsert_calls += 1
            for row in self._upsert_rows:
                self.db.rows[(row["project_id"], row["object_id"])] = row
            return SimpleNamespace(data=self._upsert_rows)

        self.db.select_calls += 1
        project_id = self.filters.get("project_id")
        object_ids = self.filters.get("object_id")
        if isinstance(object_ids, list):
            data = [
                row
                for oid in object_ids
                if (row := self.db.rows.get((project_id, oid))) is not None
            ]
            return SimpleNamespace(data=data)
        row = self.db.rows.get((project_id, object_ids))
        return SimpleNamespace(data=[row] if row else [])


class _FakeSupabase:
    def __init__(self):
        self.client = self
        self.rows = {}
        self.select_calls = 0
        self.upsert_calls = 0

    def table(self, name):
        assert name == OBJECT_LOCATIONS_TABLE
        return _FakeObjectLocationTable(self)


def _synthetic_loose_blob(size: int, *, seed: int = 0) -> tuple[str, bytes]:
    rng = random.Random(seed)
    return encode_object("blob", bytes(rng.randrange(256) for _ in range(size)))


def test_strategy_single_object_boundaries() -> None:
    strategy = _strategy(bundle_target_bytes=1024, chunk_bytes=512)

    assert strategy.plan_single("small", 1024).layout is ObjectWriteLayout.LOOSE
    assert strategy.plan_single("large", 1025).layout is ObjectWriteLayout.CHUNKED


def test_strategy_batch_routes_small_to_bundle_and_large_to_chunked() -> None:
    plan = _strategy(bundle_target_bytes=1024, chunk_bytes=512).plan_batch({
        "tiny": 16,
        "edge": 1024,
        "large": 1025,
    })

    assert plan.route_for("tiny").layout is ObjectWriteLayout.BUNDLE
    assert plan.route_for("edge").layout is ObjectWriteLayout.BUNDLE
    assert plan.route_for("large").layout is ObjectWriteLayout.CHUNKED
    assert plan.uses_location_index is True


def test_strategy_disables_bundle_and_chunk_when_location_index_is_unavailable() -> None:
    strategy = _strategy(location_index_enabled=False)

    single = strategy.plan_single("large", 50_000)
    batch = strategy.plan_batch({"small": 10, "large": 50_000})

    assert single.layout is ObjectWriteLayout.LOOSE
    assert {route.layout for route in batch.routes.values()} == {ObjectWriteLayout.LOOSE}
    assert batch.uses_location_index is False


@pytest.mark.parametrize(
    ("size_bytes", "expected_parts"),
    [
        (1024, 0),
        (1025, 3),
        (1536, 3),
        (1537, 4),
        (1024 * 1024 * 1024 + 1, 2_097_153),
    ],
)
def test_strategy_chunk_part_count_boundaries(size_bytes: int, expected_parts: int) -> None:
    plan = _strategy(bundle_target_bytes=1024, chunk_bytes=512).plan_batch({
        "object": size_bytes,
    })

    assert plan.chunk_part_count("object") == expected_parts


def test_strategy_routes_gib_objects_without_allocating_payload() -> None:
    plan = _strategy(
        bundle_target_bytes=8 * 1024 * 1024,
        chunk_bytes=8 * 1024 * 1024,
    ).plan_batch({
        "huge-object": 1024 * 1024 * 1024 + 1,
        "small-object": 128 * 1024,
    })

    assert plan.route_for("huge-object").layout is ObjectWriteLayout.CHUNKED
    assert plan.chunk_part_count("huge-object") == 129
    assert plan.route_for("small-object").layout is ObjectWriteLayout.BUNDLE


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bundle_target_bytes": 0, "chunk_bytes": 512},
        {"bundle_target_bytes": 1024, "chunk_bytes": 0},
        {"bundle_target_bytes": -1, "chunk_bytes": 512},
        {"bundle_target_bytes": 1024, "chunk_bytes": -1},
    ],
)
def test_strategy_rejects_invalid_thresholds(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        IOStorageStrategy(**kwargs)


def test_backend_single_large_write_uses_chunk_manifest_not_loose_key() -> None:
    object_id, loose = _synthetic_loose_blob(4096)
    s3 = _FakeS3(max_upload_bytes=2048)
    supabase = _FakeSupabase()
    backend = S3StorageBackend(
        s3,
        "proj",
        supabase=supabase,
        io_strategy=_strategy(bundle_target_bytes=1024, chunk_bytes=512),
    )

    backend._put(object_id, loose)

    location = supabase.rows[("proj", object_id)]
    assert location["pack_key"].startswith("chunked:")
    assert not any("/objects/" in key for key in s3.uploads)
    manifest_key = location["pack_key"].removeprefix("chunked:")
    manifest = json.loads(s3.uploads[manifest_key].decode("utf-8"))
    assert manifest["object_id"] == object_id
    assert manifest["size_bytes"] == len(loose)
    assert [chunk["offset_bytes"] for chunk in manifest["chunks"]] == list(
        range(0, len(loose), 512)
    )
    assert all(chunk["size_bytes"] <= 512 for chunk in manifest["chunks"])

    cold_backend = S3StorageBackend(s3, "proj", supabase=supabase)
    assert cold_backend.get(object_id) == loose


def test_backend_sync_put_uses_io_strategy_for_large_objects() -> None:
    object_id, loose = _synthetic_loose_blob(4096)
    s3 = _FakeS3(max_upload_bytes=2048)
    supabase = _FakeSupabase()
    backend = S3StorageBackend(
        s3,
        "proj",
        supabase=supabase,
        io_strategy=_strategy(bundle_target_bytes=1024, chunk_bytes=512),
    )

    backend.put(object_id, loose)

    location = supabase.rows[("proj", object_id)]
    assert location["pack_key"].startswith("chunked:")
    assert not any("/objects/" in key for key in s3.uploads)
    assert backend.get(object_id) == loose


@pytest.mark.asyncio
def test_object_store_put_uses_backend_io_strategy(tmp_path) -> None:
    rng = random.Random(2)
    content = bytes(rng.randrange(256) for _ in range(4096))
    s3 = _FakeS3(max_upload_bytes=2048)
    supabase = _FakeSupabase()
    backend = S3StorageBackend(
        s3,
        "proj",
        supabase=supabase,
        io_strategy=_strategy(bundle_target_bytes=1024, chunk_bytes=512),
    )
    store = ObjectStore(tmp_path / "objects", backend=backend)

    object_id = store.put(content)

    location = supabase.rows[("proj", object_id)]
    assert location["pack_key"].startswith("chunked:")
    assert not any("/objects/" in key for key in s3.uploads)
    assert store.get(object_id) == content


def test_backend_batch_mixes_bundle_and_chunked_layouts_under_one_plan() -> None:
    large_id, large_loose = _synthetic_loose_blob(4096, seed=1)
    small_id, small_loose = encode_object("blob", b"small\n")
    s3 = _FakeS3(max_upload_bytes=4096)
    supabase = _FakeSupabase()
    backend = S3StorageBackend(
        s3,
        "proj",
        supabase=supabase,
        io_strategy=_strategy(bundle_target_bytes=1024, chunk_bytes=512),
    )

    backend.put_objects({
        large_id: large_loose,
        small_id: small_loose,
    }, skip_exists=True)

    large_location = supabase.rows[("proj", large_id)]
    small_location = supabase.rows[("proj", small_id)]
    assert large_location["pack_key"].startswith("chunked:")
    assert small_location["pack_key"].endswith(".pob")
    assert any("/chunked/" in key and "/part-" in key for key in s3.uploads)
    assert any(key.endswith(".pob") for key in s3.uploads)
    assert max(s3.upload_sizes) <= 4096


def test_backend_without_location_index_keeps_large_objects_loose() -> None:
    large_id, large_loose = _synthetic_loose_blob(4096)
    small_id, small_loose = encode_object("blob", b"small\n")
    s3 = _FakeS3()
    backend = S3StorageBackend(
        s3,
        "proj",
        supabase=None,
        io_strategy=_strategy(bundle_target_bytes=1024, chunk_bytes=512),
    )

    backend.put_objects({
        large_id: large_loose,
        small_id: small_loose,
    }, skip_exists=True)

    assert len(s3.uploads) == 2
    assert all("/objects/" in key for key in s3.uploads)
    assert all("/object-bundles/" not in key for key in s3.uploads)
    assert backend.get(large_id) == large_loose
    assert backend.get(small_id) == small_loose


def test_backend_chunked_range_read_returns_logical_object_slice() -> None:
    object_id, loose = _synthetic_loose_blob(4096)
    s3 = _FakeS3(max_upload_bytes=2048)
    supabase = _FakeSupabase()
    backend = S3StorageBackend(
        s3,
        "proj",
        supabase=supabase,
        io_strategy=_strategy(bundle_target_bytes=1024, chunk_bytes=512),
    )
    backend._put(object_id, loose)

    cold_backend = S3StorageBackend(s3, "proj", supabase=supabase)
    logical_slice, total = cold_backend.get_range(
        object_id,
        start=333,
        limit=777,
    )

    assert total == len(loose)
    assert logical_slice == loose[333:1110]
    assert s3.download_file_keys
    assert all("/chunked/" in key for key in s3.download_file_keys)
