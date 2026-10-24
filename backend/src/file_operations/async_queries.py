"""Operation-owned native reads interleaved with asynchronous domain work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import ExitStack, asynccontextmanager
from threading import Lock

from src.file_operations.queries import ProductReadSession
from src.infra.execution.admission import read_admission
from src.infra.execution.runner import run_cleanup, run_owned


class AsyncReadSession:
    """One snapshot; serialize native access and join before closing its pin.

    Admission is held only during blocking queries, never while a domain waits
    for embedding or other async I/O. Native readers retain their own bounded
    caches, byte budgets and pin renewal. This object carries no new authority.
    """

    def __init__(self, stack, read):
        self._stack, self._read = stack, read
        self._lock = Lock()
        self._queries = asyncio.Lock()
        self._closed = False

    def _execute(self, query):
        with self._lock:
            if self._closed:
                raise RuntimeError("Read session is closed")
            return query(self._read)

    async def call[T](self, query: Callable[[ProductReadSession], T]) -> T:
        async with self._queries, read_admission():
            return await run_owned(self._execute, query)

    def close(self):
        # run_owned also invokes close if cancellation races successful open.
        # Taking the same lock joins an in-flight reader before releasing pins.
        with self._lock:
            if not self._closed:
                self._closed = True
                self._stack.close()


@asynccontextmanager
async def open_read_session(ops, project_id):
    def acquire():
        with ExitStack() as stack:
            read = stack.enter_context(ops.read_session(project_id))
            return AsyncReadSession(stack.pop_all(), read)

    async with read_admission():
        session = await run_owned(acquire)
    try:
        yield session
    finally:
        await run_cleanup(session.close)
