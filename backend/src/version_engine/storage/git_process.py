"""Owned, resource-limited stock Git processes for pack storage."""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

from src.infra.execution.context import checkpoint
from src.version_engine.storage.git_pack_limits import (
    MAX_METADATA_BYTES,
    configured_pack_limits,
)
from src.version_engine.storage.git_pack_limits import (
    MAX_OBJECT_BYTES as MAX_OBJECT_BYTES,
)
from src.version_engine.storage.git_pack_limits import (
    MAX_OBJECTS as MAX_OBJECTS,
)
from src.version_engine.storage.git_pack_limits import (
    MAX_SECONDS as MAX_SECONDS,
)


def environment(directory):
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(directory),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }


def spawn(command, directory, *, limits=None, **kwargs):
    limits = limits or configured_pack_limits()
    helper = str(Path(__file__).with_name("git_pack_worker.py"))
    return subprocess.Popen(
        [sys.executable, helper, "exec", str(max(limits.stored_bytes, MAX_METADATA_BYTES)), *command],
        cwd=directory,
        env=environment(directory),
        start_new_session=True,
        **kwargs,
    )


def stop(process):
    if process.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def check(deadline, directory, *, limits=None):
    limits = limits or configured_pack_limits()
    checkpoint()
    if time.monotonic() > deadline:
        raise TimeoutError("Git pack processing deadline exceeded")
    size = 0
    for root, _, files in os.walk(directory):
        for name in files:
            with suppress(FileNotFoundError):
                size += os.stat(os.path.join(root, name)).st_size
        if size > limits.scratch_bytes:
            raise ValueError("Git pack temporary disk budget exceeded")


def git_command(args):
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("stock Git is required for pack storage")
    # mmap windows and delta caches must not grow with the accepted pack size.
    return [git, "-c", "core.packedGitWindowSize=32m", "-c", "core.packedGitLimit=128m",
            "-c", "core.deltaBaseCacheLimit=64m", *args]


def command(directory, args, *, input=None, stdin=None, check_result=True, limits=None):
    limits = limits or configured_pack_limits()
    with (
        tempfile.TemporaryFile() as output,
        tempfile.TemporaryFile() as errors,
        tempfile.TemporaryFile() as source,
    ):
        if input is not None:
            source.write(input)
            source.seek(0)
            stdin = source
        process = spawn(
            git_command(args), directory, limits=limits,
            stdin=stdin or subprocess.DEVNULL, stdout=output, stderr=errors
        )
        deadline = time.monotonic() + MAX_SECONDS
        try:
            while process.poll() is None:
                check(deadline, directory, limits=limits)
                if os.fstat(output.fileno()).st_size > MAX_METADATA_BYTES:
                    raise ValueError("Git pack metadata budget exceeded")
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.05)
            check(deadline, directory, limits=limits)
            if os.fstat(output.fileno()).st_size > MAX_METADATA_BYTES:
                raise ValueError("Git pack metadata budget exceeded")
            output.seek(0)
            errors.seek(0)
            result = subprocess.CompletedProcess(
                args, process.returncode, output.read(MAX_METADATA_BYTES), errors.read(8192)
            )
            if check_result and result.returncode:
                raise ValueError(
                    "Git pack validation failed: " + result.stderr.decode("utf-8", "replace")
                )
            return result
        finally:
            stop(process)


def read_pipe(process, size, deadline):
    """Unbuffered reads remain cancellable even when a child stops producing."""
    chunks = bytearray()
    while len(chunks) < size:
        checkpoint()
        if time.monotonic() > deadline:
            raise TimeoutError("Git object read deadline exceeded")
        ready, _, _ = select.select([process.stdout], [], [], 0.05)
        if not ready:
            continue
        data = os.read(process.stdout.fileno(), min(size - len(chunks), 64 * 1024))
        if not data:
            raise ValueError("Git pack process ended before completing its response")
        chunks.extend(data)
    return bytes(chunks)


def read_line(process, deadline, limit=1024):
    line = bytearray()
    while len(line) < limit:
        byte = read_pipe(process, 1, deadline)
        if byte == b"\n":
            return bytes(line)
        line.extend(byte)
    raise ValueError("Git pack process response exceeds its bound")
