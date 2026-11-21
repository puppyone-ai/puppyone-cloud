"""Repository write channel enum and source_channel mapping."""

from __future__ import annotations

from enum import StrEnum


class WriteChannel(StrEnum):
    WORKSPACE = "workspace"
    INITIALIZATION = "initialization"
    GIT_PUSH = "git_push"
    CONTENT_API = "content_api"
    MCP = "mcp"
    CLI = "cli"
    UPLOAD = "upload"
    IMPORT = "import"
    SYNC = "sync"
    TABLE = "table"
    RESTORE = "restore"
    HEAD = "head"


_SOURCE_CHANNEL_MAP: dict[str, WriteChannel] = {
    "papi": WriteChannel.CONTENT_API,
    "content_api": WriteChannel.CONTENT_API,
    "mcp": WriteChannel.MCP,
    "access_cli": WriteChannel.CLI,
    "cli": WriteChannel.CLI,
    "upload": WriteChannel.UPLOAD,
    "import": WriteChannel.IMPORT,
    "sync": WriteChannel.SYNC,
    "table": WriteChannel.TABLE,
    "restore": WriteChannel.RESTORE,
    "initialization": WriteChannel.INITIALIZATION,
    "workspace": WriteChannel.WORKSPACE,
    "git_push": WriteChannel.GIT_PUSH,
    "head": WriteChannel.HEAD,
}


def channel_from_source(source: WriteChannel | str) -> WriteChannel:
    """Resolve a caller-supplied channel or source_channel string. Fails closed."""
    if isinstance(source, WriteChannel):
        return source
    if not isinstance(source, str):
        raise ValueError(f"Unknown repository write channel: {source!r}")
    key = source.strip().lower()
    channel = _SOURCE_CHANNEL_MAP.get(key)
    if channel is None:
        raise ValueError(f"Unknown repository write channel: {source!r}")
    return channel
