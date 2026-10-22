"""Isolated pack helper. Never imports application configuration or credentials."""

import os
import resource
import sys
from pathlib import Path

# Executed from an owned bare repository, outside the application import path.
# Import only the dependency-free contract; never Settings or storage services.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.version_engine.domain.git_pack_limits import (
    MAX_OBJECT_BYTES,
    MAX_OBJECTS,
    MAX_SECONDS,
)


def limits(max_file_bytes):
    if not 0 < max_file_bytes < 2**63:
        raise ValueError("invalid Git helper file budget")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_SECONDS, MAX_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))


def thin_bases(pack_path, object_format):
    # Only scan upstream pack entries for REF_DELTA base IDs. Do not execute
    # deltas or keep their reconstructed bodies in Python; stock Git does that.
    # A candidate can also be internal to the pack. The parent may decline it,
    # then Git determines whether all dependencies are satisfied after the scan.
    from dulwich.object_format import get_object_format
    from dulwich.pack import REF_DELTA, PackData

    seen = set()
    with PackData(pack_path, get_object_format(object_format)) as data:
        for count, entry in enumerate(data.iter_unpacked(include_comp=False), 1):
            if count > MAX_OBJECTS or entry.decomp_len > MAX_OBJECT_BYTES:
                raise ValueError("Git pack scan budget exceeded")
            if entry.pack_type_num != REF_DELTA or entry.delta_base in seen:
                continue
            seen.add(entry.delta_base)
            print(entry.delta_base.hex(), flush=True)
            if sys.stdin.readline().strip() not in {"ready", "missing"}:
                raise ValueError("invalid base admission reply")


if __name__ == "__main__":
    if sys.argv[1] == "exec":
        limits(int(sys.argv[2]))
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    elif sys.argv[1] == "thin-bases":
        # This mode is launched through exec above and inherits its limits.
        thin_bases(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("unknown pack helper operation")
