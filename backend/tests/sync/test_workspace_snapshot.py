import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.platform.workspace.provider as provider_module
from src.config import settings
from src.platform.workspace.sync_worker import SyncWorker, _extract_file_ref
from src.platform.workspace.provider import get_workspace_provider


class _FakeOps:
    def list_tree(self, project_id: str, path: str = "", max_depth: int = -1):
        return [
            SimpleNamespace(name="docs", path="docs", type="folder"),
            SimpleNamespace(name="readme.md", path="docs/readme.md", type="markdown"),
            SimpleNamespace(name="data.json", path="data.json", type="json"),
        ]

    def read_file(self, project_id: str, path: str) -> bytes:
        if path == "docs/readme.md":
            return b"# Hello\n"
        if path == "data.json":
            return b'{\n  "ok": true\n}'
        raise FileNotFoundError(path)

    def get_head_commit_id(self, project_id: str) -> str:
        return "deadbeefcafebabe"


class _FakeOpsWithBinary:
    """Fake ops that includes a binary file_ref entry."""

    def list_tree(self, project_id: str, path: str = "", max_depth: int = -1):
        return [
            SimpleNamespace(name="readme.md", path="readme.md", type="markdown"),
            SimpleNamespace(name="photo.png", path="photo.png", type="file"),
        ]

    def read_file(self, project_id: str, path: str) -> bytes:
        if path == "readme.md":
            return b"# Doc\n"
        if path == "photo.png":
            return json.dumps({
                "_type": "file_ref",
                "_s3_key": "projects/p1/uploads/photo.png",
                "filename": "photo.png",
            }).encode()
        raise FileNotFoundError(path)

    def get_head_commit_id(self, project_id: str) -> str:
        return "feedface12345678"


@pytest.mark.asyncio
async def test_sync_worker_materializes_lower_snapshot(tmp_path: Path):
    worker = SyncWorker(ops=_FakeOps(), base_dir=str(tmp_path))

    result = await worker.sync_project("proj-1")

    assert result["status"] == "ok"
    assert result["head_commit_id"] == "deadbeefcafebabe"
    assert (tmp_path / "lower" / "proj-1" / "docs" / "readme.md").read_text() == "# Hello\n"
    assert json.loads((tmp_path / "lower" / "proj-1" / "data.json").read_text()) == {"ok": True}

    metadata = json.loads((tmp_path / "lower" / "proj-1" / ".metadata.json").read_text())
    assert metadata["head_commit_id"] == "deadbeefcafebabe"
    assert metadata["file_count"] == 2


@pytest.mark.asyncio
async def test_sync_worker_downloads_binary_from_s3(tmp_path: Path, monkeypatch):
    """SyncWorker should resolve file_ref blobs to actual S3 binary content."""
    fake_s3 = MagicMock()
    fake_s3.download_file = MagicMock(return_value=b"\x89PNG_FAKE_DATA")

    monkeypatch.setattr(
        "src.platform.workspace.sync_worker.get_s3_service_instance",
        lambda: fake_s3,
    )

    worker = SyncWorker(ops=_FakeOpsWithBinary(), base_dir=str(tmp_path))
    result = await worker.sync_project("proj-binary")

    assert result["status"] == "ok"
    assert result["binary_count"] == 1
    assert result["file_count"] == 2

    photo_path = tmp_path / "lower" / "proj-binary" / "photo.png"
    assert photo_path.read_bytes() == b"\x89PNG_FAKE_DATA"

    fake_s3.download_file.assert_called_once_with("projects/p1/uploads/photo.png")


@pytest.mark.asyncio
async def test_sync_worker_fallback_when_s3_unavailable(tmp_path: Path, monkeypatch):
    """When S3 is unavailable, file_ref JSON blob is written as-is."""
    monkeypatch.setattr(
        "src.platform.workspace.sync_worker.get_s3_service_instance",
        lambda: None,
    )

    worker = SyncWorker(ops=_FakeOpsWithBinary(), base_dir=str(tmp_path))
    result = await worker.sync_project("proj-no-s3")

    assert result["binary_count"] == 0
    photo_path = tmp_path / "lower" / "proj-no-s3" / "photo.png"
    blob = json.loads(photo_path.read_text())
    assert blob["_type"] == "file_ref"


def test_extract_file_ref_valid():
    data = json.dumps({"_type": "file_ref", "_s3_key": "some/key"}).encode()
    assert _extract_file_ref(data) == "some/key"


def test_extract_file_ref_not_ref():
    assert _extract_file_ref(b'{"hello": "world"}') is None
    assert _extract_file_ref(b"plain text") is None
    assert _extract_file_ref(b"") is None


def test_get_workspace_provider_returns_singleton(monkeypatch, tmp_path: Path):
    provider_module._workspace_provider = None
    provider_module._workspace_provider_key = None

    monkeypatch.setattr(settings, "WORKSPACE_PROVIDER", "fallback", raising=False)
    monkeypatch.setattr(settings, "WORKSPACE_BASE_DIR", str(tmp_path), raising=False)

    first = get_workspace_provider()
    second = get_workspace_provider()

    assert first is second

    provider_module._workspace_provider = None
    provider_module._workspace_provider_key = None
