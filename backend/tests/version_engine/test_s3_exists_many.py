import pytest

from src.version_engine.domain.errors import ObjectNotFoundError
from src.version_engine.storage.backends.s3 import S3StorageBackend


class _S3ExistsProbe:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def file_exists(self, key: str) -> bool:
        if self.error is not None:
            raise self.error
        return False


def test_exists_many_physical_propagates_transient_probe_errors():
    backend = S3StorageBackend(
        _S3ExistsProbe(RuntimeError("supabase storage timeout")),
        "project-id",
    )

    with pytest.raises(RuntimeError, match="supabase storage timeout"):
        backend._exists_many_physical(["1" * 40])


def test_exists_many_physical_treats_not_found_as_missing():
    backend = S3StorageBackend(
        _S3ExistsProbe(ObjectNotFoundError("object not found")),
        "project-id",
    )

    assert backend._exists_many_physical(["1" * 40]) == set()
