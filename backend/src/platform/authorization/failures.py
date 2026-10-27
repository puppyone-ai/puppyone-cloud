"""Public authorization outage reasons, separate from private SDK diagnostics."""

import httpx
from postgrest.exceptions import APIError

from src.exceptions import (
    AppException,
    DatabaseSchemaOutdatedException,
    ServiceUnavailableException,
)


def authorization_unavailable(cause: Exception) -> AppException:
    # Classify only evidence supplied by a typed dependency failure. Never infer
    # a bad proxy, revoked membership or deleted project from a transport error.
    if isinstance(cause, APIError) and cause.code in {"PGRST202", "PGRST205", "42883", "42P01"}:
        return DatabaseSchemaOutdatedException()
    reason = "project_authorization_unavailable"
    if isinstance(cause, (httpx.TimeoutException, TimeoutError)):
        reason = "project_authorization_timeout"
    elif isinstance(cause, httpx.TransportError):
        reason = "project_authorization_unreachable"
    return ServiceUnavailableException(
        "Project authorization is temporarily unavailable", reason_code=reason
    )
