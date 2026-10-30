from __future__ import annotations

import re
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from src.config import settings
from src.platform.landing import tickets
from src.platform.landing.service import LandingService


class _AsciiStorage:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str]] = []

    def upload_file(self, key: str, content: bytes, content_type: str) -> None:
        # Hosted Supabase Storage rejects Unicode and some punctuation in keys.
        assert key.isascii(), "staging keys must not contain the original Unicode filename"
        assert re.fullmatch(r"[A-Za-z0-9/_.-]+", key), "unsafe staging object key"
        self.uploads.append((key, content, content_type))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "filename", "source_suffix"),
    [
        ("pdf", "中文测试.pdf", ".pdf"),
        ("pdf", "résumé (final)#?.PDF", ".pdf"),
        ("pdf", "../中文测试.pdf", ".pdf"),
        ("markdown", "使用说明.md", ".md"),
        ("openapi", "接口说明.yaml", ".yaml"),
        ("excel", "数据表.xlsx", ".xlsx"),
        ("pdf", "测试.文档", ".pdf"),
        ("markdown", "notes." + "x" * 100, ".md"),
    ],
)
async def test_preview_uses_safe_storage_keys_and_preserves_display_names(
    monkeypatch, kind: str, filename: str, source_suffix: str
) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", "landing-preview-unit-test-secret")
    storage = _AsciiStorage()
    service = LandingService(
        storage, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )  # type: ignore[arg-type]
    parsed_source_keys: list[str] = []

    async def parse(_spec, *, src_key: str, content: bytes, ticket_id: str) -> str:
        parsed_source_keys.append(src_key)
        assert content == b"synthetic public source"
        return "# Synthetic parsed content"

    monkeypatch.setattr(service, "_parse", parse)

    result = await service.preview(
        tool_kind=kind,
        filename=filename,
        content=b"synthetic public source",
        content_type="application/octet-stream",
    )

    display_name = PurePosixPath(filename).name
    payload = tickets.verify_ticket(result["ticket"])
    assert result["preview"]["filename"] == display_name
    assert payload["src_name"] == display_name
    assert payload["md_name"] == PurePosixPath(display_name).stem + ".md"
    assert len(storage.uploads) == 2
    source_key, source_bytes, _ = storage.uploads[0]
    parsed_key, parsed_bytes, _ = storage.uploads[1]
    assert source_key.endswith(source_suffix)
    assert parsed_source_keys == [source_key]
    assert payload["md_key"] == parsed_key
    assert source_bytes == b"synthetic public source"
    assert parsed_bytes == b"# Synthetic parsed content"
