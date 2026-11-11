"""Pack admission and execution budgets; safe to import in an isolated helper.

Receive policy can be lowered without changing the readable storage format.
Increasing the stored-pack profile requires corresponding scratch capacity on
every serving reader. Limits are configuration, not a capacity benchmark.
"""

from dataclasses import dataclass

MIB = 1024**2
GIB = 1024**3
MAX_RECEIVE_PACK_BYTES = 2 * GIB
DEFAULT_STORED_PACK_BYTES = 4 * GIB
DEFAULT_PACK_SCRATCH_BYTES = 9 * GIB
MAX_OBJECT_BYTES = 64 * MIB
MAX_OBJECTS = 100_000
MAX_SECONDS = 300
MAX_METADATA_BYTES = 32 * MIB
MAX_RECEIVE_COMMAND_BYTES = 16 * MIB

# Stable codes for a push refused by a fixed budget. They are the cross-
# repository contract contracts/cloud-git-acceptance-v1.json: Desktop tells a
# user to reduce the content instead of retrying, because the same content can
# never pass. Add a code here and in the contract together.
GIT_PACK_TOO_LARGE = "git_pack_too_large"
GIT_STORED_PACK_TOO_LARGE = "git_stored_pack_too_large"
GIT_OBJECT_COUNT_LIMIT = "git_object_count_limit"
GIT_SINGLE_OBJECT_LIMIT = "git_single_object_limit"
GIT_GRAPH_LIMIT = "git_graph_limit"
GIT_PUBLISHED_OBJECT_COUNT_LIMIT = "git_published_object_count_limit"
GIT_PROCESSING_DISK_LIMIT = "git_processing_disk_limit"
GIT_PROCESSING_METADATA_LIMIT = "git_processing_metadata_limit"
GIT_PROCESSING_OUTPUT_LIMIT = "git_processing_output_limit"
GIT_LIMIT_CODES = frozenset(
    {
        GIT_PACK_TOO_LARGE,
        GIT_STORED_PACK_TOO_LARGE,
        GIT_OBJECT_COUNT_LIMIT,
        GIT_SINGLE_OBJECT_LIMIT,
        GIT_GRAPH_LIMIT,
        GIT_PUBLISHED_OBJECT_COUNT_LIMIT,
        GIT_PROCESSING_DISK_LIMIT,
        GIT_PROCESSING_METADATA_LIMIT,
        GIT_PROCESSING_OUTPUT_LIMIT,
    }
)


class GitLimitExceeded(ValueError):
    """Work exceeds a fixed budget; retrying the same content cannot pass.

    A ValueError with an unchanged message, so every existing refusal path and
    reader keeps working; ``code`` is what a client may rely on.
    """

    def __init__(self, code, message):
        if code not in GIT_LIMIT_CODES:
            raise AssertionError(f"unregistered Git limit code {code!r}")
        super().__init__(message)
        self.code = code


def refusal_reason(error):
    """The human reason for a Git client, with its stable code appended."""
    code = error.code if isinstance(error, GitLimitExceeded) else None
    return f"{error} (puppyone-refusal={code})" if code else str(error)


@dataclass(frozen=True)
class PackLimits:
    receive_bytes: int = MAX_RECEIVE_PACK_BYTES
    stored_bytes: int = DEFAULT_STORED_PACK_BYTES
    scratch_bytes: int = DEFAULT_PACK_SCRATCH_BYTES

    def __post_init__(self):
        for name in ("receive_bytes", "stored_bytes", "scratch_bytes"):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value < 2**63:
                raise ValueError(f"Git {name} must be a positive finite byte limit")
        if self.receive_bytes > MAX_RECEIVE_PACK_BYTES:
            raise ValueError("Git receive limit cannot exceed 2 GiB per push")
        if self.stored_bytes < self.receive_bytes:
            raise ValueError("Git stored-pack budget must cover the receive limit")
        if self.scratch_bytes < 2 * self.stored_bytes + 2 * MAX_METADATA_BYTES:
            raise ValueError("Git scratch must cover two stored packs and index overhead")
