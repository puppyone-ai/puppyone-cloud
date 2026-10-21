"""Optional bounded wire compression preserves the public native file contract."""

import base64
import zlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.platform.access.adapters.agent.runtime.filesystem.port import FilesystemError
from src.platform.access.adapters.agent.runtime.filesystem.sandbox import SandboxFilesystem

pytestmark = pytest.mark.asyncio


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
