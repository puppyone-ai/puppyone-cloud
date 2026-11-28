"""Enforcement helpers for repository write policy."""

from __future__ import annotations

from src.exceptions import AppException, ErrorCode
from src.platform.repository_policy.channels import WriteChannel, channel_from_source
from src.platform.repository_policy.loader import get_repository_policy

BLOCKED_ERROR_CODE = "repository_writes_through_workspace"
BLOCKED_MESSAGE = "这个项目只能在它的云端工作区里修改"


class RepositoryWriteBlockedError(AppException):
    """Raised when the active repository policy forbids writes on a channel."""

    def __init__(self, channel: WriteChannel, *, message: str = BLOCKED_MESSAGE):
        self.channel = channel
        self.reason_code = BLOCKED_ERROR_CODE
        super().__init__(
            code=ErrorCode.VERSION_CONFLICT,
            message=message,
            status_code=409,
            details={
                "code": BLOCKED_ERROR_CODE,
                "message": message,
                "channel": channel.value,
            },
        )


def is_write_allowed(channel: WriteChannel | str) -> bool:
    """Return True if the active policy allows writes on the given channel."""
    resolved = channel_from_source(channel)
    return get_repository_policy().allows_write(resolved)


def require_write(channel: WriteChannel | str) -> None:
    """Enforce the active write policy, raising HTTP 409 when the channel is closed."""
    resolved = channel_from_source(channel)
    if not get_repository_policy().allows_write(resolved):
        raise RepositoryWriteBlockedError(resolved)


def git_refusal_message() -> str:
    """Return the Git wire refusal message for blocked pushes."""
    return BLOCKED_MESSAGE
