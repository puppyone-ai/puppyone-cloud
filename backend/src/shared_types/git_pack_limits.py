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
