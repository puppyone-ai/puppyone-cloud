"""Binary trailers must not make a project's SQL text projection unindexable."""

import pytest

from src.version_engine.derived.text_indexer import _decode_for_index


@pytest.mark.parametrize("offset", [0, 8191, 8192, 8193, 32768])
def test_nul_anywhere_excludes_blob_from_sql_text_index(offset):
    data = b"plain text\n" * (offset // 11) + b"a" * (offset % 11) + b"\x00trailer"
    assert _decode_for_index(data) is None


@pytest.mark.parametrize("text", ["", "plain text\n", "中文🙂\n" * 4096])
def test_valid_utf8_content_is_preserved(text):
    assert _decode_for_index(text.encode("utf-8")) == text


def test_latin1_content_without_nul_remains_indexable():
    assert _decode_for_index(b"caf\xe9") == "café"
