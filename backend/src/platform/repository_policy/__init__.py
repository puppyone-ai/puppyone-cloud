"""Repository write & backup policy configuration and enforcement."""

from src.platform.repository_policy.channels import WriteChannel, channel_from_source
from src.platform.repository_policy.enforce import (
    BLOCKED_ERROR_CODE,
    BLOCKED_MESSAGE,
    RepositoryWriteBlockedError,
    git_refusal_message,
    is_write_allowed,
    require_write,
)
from src.platform.repository_policy.loader import (
    BUILTIN_PROFILES,
    PROFILES_DIR,
    get_repository_policy,
    load_named_profile,
    load_policy_from_path,
    load_repository_policy,
    reset_policy_cache,
)
from src.platform.repository_policy.model import BackupPolicy, RepositoryPolicy, WritesPolicy

__all__ = [
    "BLOCKED_ERROR_CODE",
    "BLOCKED_MESSAGE",
    "BUILTIN_PROFILES",
    "PROFILES_DIR",
    "BackupPolicy",
    "RepositoryPolicy",
    "RepositoryWriteBlockedError",
    "WriteChannel",
    "WritesPolicy",
    "channel_from_source",
    "get_repository_policy",
    "git_refusal_message",
    "is_write_allowed",
    "load_named_profile",
    "load_policy_from_path",
    "load_repository_policy",
    "require_write",
    "reset_policy_cache",
]
