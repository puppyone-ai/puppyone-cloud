"""Standard Git pack ownership, validation and indexed object reads.

No incoming object is converted into a loose representation for persistence.
Only external thin-pack bases may be imported from the admitted old snapshot.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from src.infra.execution.context import checkpoint
from src.version_engine.storage import git_process as proc
from src.version_engine.storage.git_pack_limits import configured_pack_limits
from src.version_engine.write_engine.git_object_format import encode_object, hash_object


class GitPack:
    def __init__(self, object_format, resolve_base=None, *, limits=None):
        if object_format not in {"sha1", "sha256"}:
            raise ValueError("unsupported repository object format")
        self.format = object_format
        self.limits = limits or configured_pack_limits()
        self.resolve_base = resolve_base
        self._temporary = tempfile.TemporaryDirectory(prefix="puppyone-pack-")
        self.directory = Path(self._temporary.name)
        self.path = None
        self.objects = {}
        self._reader = None
        self._errors = None
        self.digest = None
        self._read_lock = threading.Lock()
        try:
            proc.command(
                self.directory,
                ["init", "--bare", "--template=", f"--object-format={object_format}", "."],
                limits=self.limits,
            )
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._reader is not None:
            proc.stop(self._reader)
            self._reader = None
        if self._errors is not None:
            self._errors.close()
            self._errors = None
        self._temporary.cleanup()

    def read(self, handle, *, max_input_bytes=None):
        limit = self.limits.receive_bytes
        if max_input_bytes is not None:
            if type(max_input_bytes) is not int or max_input_bytes <= 0:
                raise ValueError("Git receive limit must be positive")
            limit = min(limit, max_input_bytes)
        self._read(handle, limit)

    def read_stored(self, handle):
        """Cold reads use storage capacity, never the current upload policy."""
        self._read(handle, self.limits.stored_bytes)

    def _read(self, handle, input_limit):
        source = self.directory / "incoming.pack"
        size = 0
        with source.open("wb") as output:
            while block := handle.read(1024 * 1024):
                checkpoint()
                size += len(block)
                if size > input_limit:
                    raise ValueError(f"Git pack exceeds the {input_limit}-byte limit for this operation")
                output.write(block)
        if size == 0:
            source.unlink()
            return
        args = [
            "-c",
            "pack.threads=1",
            "index-pack",
            "--stdin",
            "--fix-thin",
            "--fsck-objects",
            f"--max-input-size={input_limit}",
        ]
        with source.open("rb") as stream:
            result = proc.command(self.directory, args, stdin=stream, check_result=False,
                                  limits=self.limits)
        if result.returncode and b"unresolved delta" in result.stderr and self.resolve_base:
            self._import_bases(source)
            with source.open("rb") as stream:
                result = proc.command(self.directory, args, stdin=stream, check_result=False,
                                      limits=self.limits)
        if result.returncode:
            raise ValueError("invalid pack: " + result.stderr.decode("utf-8", "replace"))
        packs = list((self.directory / "objects/pack").glob("*.pack"))
        if len(packs) != 1:
            raise ValueError("Git did not produce one complete pack")
        self.path = packs[0]
        if self.path.stat().st_size > self.limits.stored_bytes:
            raise ValueError("completed Git pack exceeds the storage processing budget")
        # index-pack already validated the stream and object hashes. Read its
        # index instead of running verify-pack (which inflates the pack again).
        with self.path.with_suffix(".idx").open("rb") as index:
            listing = proc.command(
                self.directory, ["show-index", f"--object-format={self.format}"], stdin=index,
                limits=self.limits,
            ).stdout
        positions = []
        width = 40 if self.format == "sha1" else 64
        for line in listing.splitlines():
            row = line.split()
            if len(row) != 3 or not re.fullmatch(f"[0-9a-f]{{{width}}}", row[1].decode()):
                raise ValueError("invalid Git object index")
            positions.append((int(row[0]), row[1].decode()))
            if len(positions) > proc.MAX_OBJECTS:
                raise ValueError("Git object count budget exceeded")
        positions.sort()
        if not positions:
            # Valid ref-only pushes carry an empty pack; no object write needed.
            self.path.unlink()
            self.path = None
            source.unlink()
            return
        trailer = self.path.stat().st_size - width // 2
        for i, (offset, oid) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else trailer
            if offset < 12 or end <= offset:
                raise ValueError("invalid Git pack object offset")
            self.objects[oid] = (None, offset, None, end - offset)
        # cat-file reads logical sizes from the index without retaining bodies.
        sizes = proc.command(
            self.directory,
            ["cat-file", "--batch-check"],
            input="".join(oid + "\n" for oid in self.objects).encode(),
            limits=self.limits,
        ).stdout
        count = 0
        for line in sizes.splitlines():
            oid, kind, size = line.decode().split()
            _, offset, _, packed_size = self.objects[oid]
            if (
                kind not in {"blob", "tree", "commit", "tag"}
                or not 0 <= int(size) <= proc.MAX_OBJECT_BYTES
            ):
                raise ValueError("Git single-object budget exceeded")
            self.objects[oid] = (kind, offset, int(size), packed_size)
            count += 1
        if count != len(self.objects):
            raise ValueError("incomplete Git object metadata")
        with self.path.open("rb") as stream:
            self.digest = hashlib.file_digest(stream, "sha256").hexdigest()
        source.unlink()

    def _import_bases(self, source):
        helper = str(Path(__file__).with_name("git_pack_worker.py"))
        with tempfile.TemporaryFile() as errors:
            child = proc.spawn(
                [sys.executable, helper, "thin-bases", str(source), self.format],
                self.directory, limits=self.limits,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                bufsize=0,
            )
            deadline = time.monotonic() + proc.MAX_SECONDS
            imported = set()
            try:
                while child.poll() is None:
                    proc.check(deadline, self.directory, limits=self.limits)
                    try:
                        oid = proc.read_line(child, deadline).decode("ascii")
                    except ValueError:
                        if child.wait(timeout=1) == 0:
                            break
                        raise
                    width = 40 if self.format == "sha1" else 64
                    if (
                        not re.fullmatch(f"[0-9a-f]{{{width}}}", oid)
                        or len(imported) >= proc.MAX_OBJECTS
                    ):
                        raise ValueError("invalid external delta base request")
                    try:
                        kind, body = self.resolve_base(oid)
                    except (KeyError, PermissionError):
                        child.stdin.write(b"missing\n")
                        continue
                    if (
                        len(body) > proc.MAX_OBJECT_BYTES
                        or hash_object(kind, body, object_format=self.format) != oid
                    ):
                        raise ValueError("invalid external delta base")
                    dest = self.directory / "objects" / oid[:2] / oid[2:]
                    dest.parent.mkdir(exist_ok=True)
                    dest.write_bytes(encode_object(kind, body, object_format=self.format)[1])
                    imported.add(oid)
                    child.stdin.write(b"ready\n")
                if child.wait(timeout=1):
                    errors.seek(0)
                    raise ValueError(
                        "cannot resolve thin pack: " + errors.read(2048).decode("utf-8", "replace")
                    )
            finally:
                proc.stop(child)

    def get(self, oid):
        while not self._read_lock.acquire(timeout=0.05):
            checkpoint()
        try:
            checkpoint()
            try:
                return self._get(oid)
            except BaseException:
                # A cancelled/partial batch response cannot be reused by the
                # next caller. Its immutable pack remains safe to reopen.
                if self._reader is not None:
                    proc.stop(self._reader)
                    self._reader = None
                if self._errors is not None:
                    self._errors.close()
                    self._errors = None
                raise
        finally:
            self._read_lock.release()

    def _get(self, oid):
        if oid not in self.objects:
            if self.resolve_base is None:
                raise KeyError(oid)
            return self.resolve_base(oid)
        if self._reader is not None and self._reader.poll() is not None:
            proc.stop(self._reader)
            self._reader = None
            self._errors.close()
            self._errors = None
        if self._reader is None:
            self._errors = tempfile.TemporaryFile()  # noqa: SIM115 — owned until close()/reader failure
            self._reader = proc.spawn(
                proc.git_command(["cat-file", "--batch"]),
                self.directory, limits=self.limits,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._errors,
                bufsize=0,
            )
        deadline = time.monotonic() + proc.MAX_SECONDS
        self._reader.stdin.write(oid.encode() + b"\n")
        line = proc.read_line(self._reader, deadline).split()
        kind, _, size, _ = self.objects[oid]
        if line != [oid.encode(), kind.encode(), str(size).encode()]:
            raise ValueError("Git object metadata mismatch")
        body = proc.read_pipe(self._reader, size, deadline)
        if proc.read_pipe(self._reader, 1, deadline) != b"\n":
            raise ValueError("Git object response is truncated")
        return kind, body
