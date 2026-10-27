"""Cross-platform advisory file lock shim.

The Git view cache warmer needs to serialize concurrent rebuilds of the
same per-view bare repo directory; POSIX uses ``fcntl.flock`` and
Windows uses ``msvcrt.locking``. Two systems, same intent — this module
gives us one API so the caller never has to branch.

Why not pull in ``portalocker`` / ``filelock``? Because (a) only ONE
call site in the codebase needs this, (b) the contract is small enough
that a stdlib-only shim is ~30 lines, and (c) adding a third-party
locking dep for a single use is more weight than it's worth.

Usage::

    with file_exclusive_lock(lock_path):
        # critical section — only one process at a time
        ...

The context manager blocks until the lock is held, releases on exit
(including exception paths). Lock files are kept on disk between
acquisitions — they're a sync primitive, not state.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

_IS_WINDOWS = sys.platform.startswith("win")


@contextmanager
def file_exclusive_lock(lock_path: Path):
    """Hold an exclusive advisory lock on ``lock_path`` for the block.

    The file is created (touched) if it doesn't exist; the lock is
    acquired blocking, and released when the context exits — both on
    normal completion and on exception.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Open for read/write/binary so both POSIX and Windows lock APIs
    # accept the file descriptor without complaining about mode.
    fh = lock_path.open("a+b")
    try:
        _acquire(fh)
        try:
            yield
        finally:
            _release(fh)
    finally:
        fh.close()


@contextmanager
def try_file_exclusive_lock(lock_path: Path):
    """Attempt an exclusive lock once, yielding False instead of blocking."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+b")
    acquired = False
    try:
        acquired = _try_acquire(fh)
        yield acquired
    finally:
        if acquired:
            _release(fh)
        fh.close()


if _IS_WINDOWS:
    import msvcrt

    def _acquire(fh) -> None:
        # msvcrt.locking is per-byte; locking byte 0 with a large length
        # is the conventional way to do whole-file exclusive locking.
        # LK_LOCK blocks (with retries) until acquired.
        fh.seek(0)
        # Use 1 byte at offset 0; Windows doesn't need to lock the whole
        # range — just a single byte we agree on. LK_LOCK retries
        # internally up to ~10s and then raises OSError; we re-loop
        # forever to match POSIX flock blocking semantics.
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                # LK_LOCK retried to its internal cap and gave up;
                # retry from scratch until we get the lock.
                continue

    def _release(fh) -> None:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            # Releasing a lock we don't hold or that's already gone is
            # not an error condition we care about — the file handle
            # close will clean up regardless.
            pass

    def _try_acquire(fh) -> bool:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

else:
    import fcntl

    def _acquire(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_EX)

    def _release(fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)

    def _try_acquire(fh) -> bool:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
