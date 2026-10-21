"""Optional bounded wire compression preserves the public native file contract."""

import base64
import os
import zlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform.access.adapters.agent.runtime.filesystem.port import FilesystemError
from src.platform.access.adapters.agent.runtime.filesystem.sandbox import SandboxFilesystem

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("data", [b"small", b"source code\n" * 1000, os.urandom(4096)])
async def test_write_encoding_requires_negotiation_and_preserves_native_content(supported, data):
    operation = {
        "action": "write",
        "path": "file.py",
        "content_base64": base64.b64encode(data).decode(),
        "expected_revision": "a" * 64,
    }
    channel = SimpleNamespace(
        service_receipt={"write_encodings": ["deflate"]} if supported else {},
        control=AsyncMock(return_value={"result": {"revision": "new-revision"}}),
    )
    await SandboxFilesystem().operation(channel, operation)
    action, sent = channel.control.call_args.args
    assert action == "files"
    assert sent["expected_revision"] == operation["expected_revision"]
    raw = base64.b64decode(sent["content_base64"])
    if sent.get("content_encoding") == "deflate":
        assert supported and len(raw) < len(data)
        assert zlib.decompress(raw) == data
    else:
        assert raw == data
    assert "content_encoding" not in operation
    if not supported:
        assert sent == operation


@pytest.mark.parametrize(
    "data", ["invalid!", base64.b64encode(b"." * (8 * 1024 * 1024 + 1)).decode()]
)
async def test_invalid_or_oversized_write_is_rejected_before_compression(data):
    channel = SimpleNamespace(service_receipt={"write_encodings": ["deflate"]}, control=AsyncMock())
    with pytest.raises(FilesystemError, match="file_operation_rejected"):
        await SandboxFilesystem().operation(channel, {"action": "write", "content_base64": data})
    channel.control.assert_not_called()


@pytest.mark.parametrize("packed", [False, True])
async def test_plain_and_compressed_workers_return_identical_api_content(packed):
    data = b"example source code\n" * 1000
    value = {
        "content_base64": base64.b64encode(zlib.compress(data) if packed else data).decode(),
        "revision": "unchanged-content-sha",
        "size": len(data),
    }
    if packed:
        value["content_encoding"] = "deflate"
    channel = SimpleNamespace(control=AsyncMock(return_value={"result": value}))
    result = await SandboxFilesystem().operation(channel, {"action": "read", "path": "file.py"})
    assert base64.b64decode(result["content_base64"]) == data
    assert "content_encoding" not in result
    assert result["revision"] == value["revision"]
    assert result["size"] == len(data)


@pytest.mark.parametrize(
    "body",
    [
        b"invalid",
        zlib.compress(b"truncated")[:-2],
        zlib.compress(b"one") + b"trailing",
        zlib.compress(b"." * (8 * 1024 * 1024 + 1)),
    ],
)
async def test_corrupt_truncated_trailing_and_oversized_transfers_are_rejected(body):
    channel = SimpleNamespace(
        control=AsyncMock(
            return_value={
                "result": {
                    "content_encoding": "deflate",
                    "content_base64": base64.b64encode(body).decode(),
                }
            }
        )
    )
    with pytest.raises(FilesystemError):
        await SandboxFilesystem().operation(channel, {"action": "read", "path": "file.py"})
