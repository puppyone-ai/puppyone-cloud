"""Count actual database HTTP attempts, including exceptions and retries.

Context is copied by asyncio.to_thread; the accumulator is shared and locked.
No query strings, SQL bodies, tokens or user content enter measurements.
"""

import re
import threading
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import httpx
from loguru import logger

_trace = ContextVar("database_trace", default=None)
_stage = ContextVar("database_stage", default="control")


@dataclass
class DatabaseTrace:
    run_id: str
    attempts: int = 0
    inflight: int = 0
    max_inflight: int = 0
    failures: int = 0
    seconds: float = 0
    operations: Counter = field(default_factory=Counter)
    stages: Counter = field(default_factory=Counter)
    phase_seconds: Counter = field(default_factory=Counter)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def begin(self, operation):
        with self.lock:
            self.attempts += 1
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.operations[operation] += 1
            self.stages[_stage.get()] += 1

    def end(self, elapsed, failed):
        with self.lock:
            self.inflight -= 1
            self.seconds += elapsed
            self.failures += int(failed)

    def report(self):
        with self.lock:
            return {
                "run_id": self.run_id,
                "attempts": self.attempts,
                "max_inflight": self.max_inflight,
                "inflight": self.inflight,
                "failures": self.failures,
                "request_seconds": self.seconds,
                "operations": dict(self.operations),
                "stages": dict(self.stages),
                "phase_seconds": dict(self.phase_seconds),
            }

    def duration(self, phase, seconds):
        with self.lock:
            self.phase_seconds[phase] += seconds


@contextmanager
def database_trace(trace):
    token = _trace.set(trace)
    try:
        yield trace
    finally:
        _trace.reset(token)


@contextmanager
def database_stage(name):
    token = _stage.set(name)
    start = time.monotonic()
    try:
        yield
    finally:
        if trace := _trace.get():
            with trace.lock:
                trace.phase_seconds[name] += time.monotonic() - start
        _stage.reset(token)


class DatabaseTransport(httpx.BaseTransport):
    def __init__(self, transport=None, *, path_prefix="/rest/v1/", aggregate=None):
        self.transport = transport or httpx.HTTPTransport()
        self.path_prefix = path_prefix
        self.aggregate = aggregate

    def handle_request(self, request):
        traces = [trace for trace in (_trace.get(), self.aggregate) if trace is not None]
        if len(traces) == 2 and traces[0] is traces[1]:
            traces.pop()
        if not request.url.path.startswith(self.path_prefix):
            return self.transport.handle_request(request)
        operation = request.method + " " + request.url.path.removeprefix(self.path_prefix)
        for trace in traces:
            trace.begin(operation)
        start, failed = time.monotonic(), True
        try:
            response = self.transport.handle_request(request)
            response.read()
            failed = response.status_code >= 400
            if failed:
                try:
                    code = response.json().get("code")
                except (ValueError, AttributeError):
                    code = None
                logger.bind(
                    component="database", operation=operation,
                    elapsed_ms=round((time.monotonic() - start) * 1000, 2),
                    status_code=response.status_code,
                    error_code=code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{5,10}", code) else None,
                ).error("database_http_response_failed")
            return response
        except Exception as exc:
            # Failed requests never reach HTTPX's response log. Preserve the
            # operation and timing without credentials, query/body or arbitrary
            # exception text (which may contain customer data).
            logger.bind(
                component="database",
                operation=operation,
                elapsed_ms=round((time.monotonic() - start) * 1000, 2),
                error_type=type(exc).__name__,
                timeout=request.extensions.get("timeout"),
            ).error("database_http_request_failed")
            raise
        finally:
            for trace in traces:
                trace.end(time.monotonic() - start, failed)

    def close(self):
        self.transport.close()


def instrument_httpx_client(client):
    """Wrap the resolved HTTPX transports, preserving proxy/NO_PROXY routing.

    HTTPX has no public error-completion hook. This small version-tested seam
    instruments existing transports instead of rebuilding or bypassing proxies.
    """

    def wrap(transport):
        if transport is None or isinstance(transport, DatabaseTransport):
            return transport
        return DatabaseTransport(transport)

    client._transport = wrap(client._transport)
    client._mounts = {pattern: wrap(transport) for pattern, transport in client._mounts.items()}
    return client
