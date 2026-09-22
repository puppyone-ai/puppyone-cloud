"""Breaking repository-target contract gate for installed clients."""

from __future__ import annotations

from fastapi import Header

from src.exceptions import AppException, ErrorCode

REPOSITORY_TARGET_CONTRACT_VERSION = 2
REPOSITORY_TARGET_CONTRACT_HEADER = "X-PuppyOne-Repository-Contract"


def require_repository_target_contract(
    version: int | None = Header(
        default=None,
        alias=REPOSITORY_TARGET_CONTRACT_HEADER,
    ),
) -> int:
    if version != REPOSITORY_TARGET_CONTRACT_VERSION:
        raise AppException(
            code=ErrorCode.CLIENT_UPGRADE_REQUIRED,
            status_code=426,
            message="CLIENT_UPGRADE_REQUIRED",
            details={
                "required_repository_contract": REPOSITORY_TARGET_CONTRACT_VERSION,
            },
        )
    return version
