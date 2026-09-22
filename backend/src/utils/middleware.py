from __future__ import annotations

import re
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from loguru import logger

from src.utils.request_context import (
    client_ip_var,
    method_var,
    path_var,
    request_id_var,
    project_access_cache_var,
    entitlement_snapshot_cache_var,
)


_LEGACY_GIT_SECRET_PATH = re.compile(
    r"^/git/ap/[^/]+\.git(?P<suffix>/.*)?$",
)


def _sanitize_path_for_access_log(request: Request) -> str:
    path = str(request.url.path)
    match = _LEGACY_GIT_SECRET_PATH.fullmatch(path)
    if match is None:
        return path
    return f"/git/ap/<redacted>.git{match.group('suffix') or ''}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    - Ensure every request has X-Request-Id (propagate if provided).
    - Store request context in contextvars for logging (request_id/method/path/client_ip).
    - Emit a structured access log line with latency + status_code.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()

        incoming = request.headers.get("x-request-id")
        request_id = incoming.strip() if incoming else uuid.uuid4().hex

        # Fill contextvars for the whole request lifetime
        token_rid = request_id_var.set(request_id)
        token_m = method_var.set(request.method)
        token_p = path_var.set(_sanitize_path_for_access_log(request))
        token_ip = client_ip_var.set(request.client.host if request.client else None)
        token_pac = project_access_cache_var.set({})
        token_ec = entitlement_snapshot_cache_var.set({})

        response = await call_next(request)

        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-Id"] = request_id

        logger.bind(
            status_code=response.status_code,
            latency_ms=round(elapsed_ms, 2),
        ).info("access")

        # Always reset contextvars to avoid leaking between requests
        request_id_var.reset(token_rid)
        method_var.reset(token_m)
        path_var.reset(token_p)
        client_ip_var.reset(token_ip)
        project_access_cache_var.reset(token_pac)
        entitlement_snapshot_cache_var.reset(token_ec)

        return response
