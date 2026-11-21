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
    # A product write an Agent sends itself is a content write.
    "agent": WriteChannel.CONTENT_API,
    # The Git adapter's own name for a push.
    "access_git": WriteChannel.GIT_PUSH,
    "mcp": WriteChannel.MCP,
    "access_cli": WriteChannel.CLI,
    "cli": WriteChannel.CLI,
    "upload": WriteChannel.UPLOAD,
    "import": WriteChannel.IMPORT,
    "sync": WriteChannel.SYNC,
    "table": WriteChannel.TABLE,
    "restore": WriteChannel.RESTORE,
    "git_push": WriteChannel.GIT_PUSH,
    "head": WriteChannel.HEAD,
}
# Filling a new Project and backing up its Workspace are facts the write
# entry points derive (an initialization grant or an empty repository; an
# admitted Workspace publication), never labels a caller may claim.
_DERIVED_CHANNELS = frozenset({WriteChannel.INITIALIZATION, WriteChannel.WORKSPACE})


def product_write_channel(
    requested: WriteChannel | str, *, initializing: bool, repository_empty: bool
) -> WriteChannel:
    """The channel a product write counts as.

    A write with an initialization grant, or into a repository with no commit
    yet, fills a new Project. Anything else counts as the channel it came
    through; a caller cannot ask to be counted as initialization or as a
    Workspace.
    """
    channel = channel_from_source(requested)
    if channel in _DERIVED_CHANNELS:
        raise ValueError(f"Repository write channel {channel.value!r} is derived, not requested")
    if channel is not WriteChannel.RESTORE and (initializing or repository_empty):
        return WriteChannel.INITIALIZATION
    return channel


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
