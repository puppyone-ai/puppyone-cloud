"""Application binding for the pure Git pack resource contract."""

from src.version_engine.domain.git_pack_limits import (
    MAX_METADATA_BYTES,
    MAX_OBJECT_BYTES,
    MAX_OBJECTS,
    MAX_RECEIVE_COMMAND_BYTES,
    MAX_SECONDS,
    PackLimits,
)

__all__ = [
    "MAX_METADATA_BYTES",
    "MAX_OBJECTS",
    "MAX_OBJECT_BYTES",
    "MAX_RECEIVE_COMMAND_BYTES",
    "MAX_SECONDS",
    "PackLimits",
    "configured_pack_limits",
]


def configured_pack_limits():
    # Application settings (and credentials) are never imported by the worker.
    from src.config import settings

    return PackLimits(
        receive_bytes=settings.GIT_MAX_RECEIVE_PACK_BYTES,
        stored_bytes=settings.GIT_MAX_STORED_PACK_BYTES,
        scratch_bytes=settings.GIT_PACK_SCRATCH_BYTES,
    )
