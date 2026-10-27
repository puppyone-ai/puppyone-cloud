"""Cached storage backend durability regressions."""

from __future__ import annotations

import pytest

from src.version_engine.storage.backends import s3 as s3_backend
from src.version_engine.storage.backends.s3 import CachedStorageBackend
from src.version_engine.storage.object_store import StorageBackend
from src.version_engine.write_engine.git_object_format import encode_object


class _MemoryBackend(StorageBackend):
    def __init__(self, project_id: str):
        self._project_id = project_id
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []

    def get(self, h: str) -> bytes:
        return self.objects[h]

    def put(self, h: str, loose_bytes: bytes) -> None:
        self.put_calls.append(h)
        self.objects[h] = loose_bytes

    def exists(self, h: str) -> bool:
        return h in self.objects

    def all_hashes(self) -> list[str]:
        return list(self.objects)

    def count(self) -> tuple[int, int]:
        return len(self.objects), sum(len(v) for v in self.objects.values())

    def delete(self, h: str) -> bool:
        return self.objects.pop(h, None) is not None


class _FailingBatchBackend(_MemoryBackend):
    def __init__(self, project_id: str):
        super().__init__(project_id)
        self.fail_batch = True

    def put_objects(
        self,
        objects: dict[str, bytes],
        *,
        concurrency: int = 20,
        skip_exists: bool = False,
    ) -> None:
        if self.fail_batch:
            raise RuntimeError("batch flush failed")
        for h, data in objects.items():
            self.put(h, data)


def _reset_cache(monkeypatch) -> None:
    monkeypatch.setattr(s3_backend, "_global_cache", None)


def test_cache_hit_in_one_project_does_not_skip_put_in_another(monkeypatch) -> None:
    _reset_cache(monkeypatch)
    object_id, loose = encode_object("blob", b"shared bytes")
    project_a = CachedStorageBackend(_MemoryBackend("project-a"))
    project_b = CachedStorageBackend(_MemoryBackend("project-b"))

    project_a.put(object_id, loose)
    assert project_a.get(object_id) == loose

    project_b.put(object_id, loose)

    assert project_b._inner.objects[object_id] == loose
    assert project_b._inner.put_calls == [object_id]


def test_failed_write_batch_does_not_poison_existence_cache(monkeypatch) -> None:
    _reset_cache(monkeypatch)
    object_id, loose = encode_object("blob", b"retry me")
    inner = _FailingBatchBackend("project-a")
    backend = CachedStorageBackend(inner)

    with pytest.raises(RuntimeError, match="batch flush failed"):
        with backend.stage_object_writes() as batch:
            backend.put(object_id, loose)
            assert backend.exists(object_id) is True
            batch.flush()

    assert backend.exists(object_id) is False

    inner.fail_batch = False
    backend.put(object_id, loose)

    assert inner.objects[object_id] == loose
    assert inner.put_calls == [object_id]
