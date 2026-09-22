from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import Header, Response

from src.exceptions import AppException, ErrorCode

IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def require_idempotency_key(
    value: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """Return one canonical UUIDv4 idempotency key or fail explicitly."""

    if value is None or not value.strip():
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=400,
            message="Idempotency-Key is required",
            details={"code": "idempotency_key_required"},
        )
    candidate = value.strip()
    try:
        parsed = UUID(candidate)
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.version != 4
        or str(parsed) != candidate
        or IDEMPOTENCY_KEY_PATTERN.fullmatch(candidate) is None
    ):
        raise AppException(
            code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            message="Idempotency-Key must be a canonical UUIDv4",
            details={"code": "idempotency_key_invalid"},
        )
    return candidate


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def mark_idempotency_replay(response: Response, *, replayed: bool) -> None:
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"


def raise_idempotency_outcome(outcome: str, *, resource: str) -> None:
    if outcome == "conflict":
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Idempotency-Key was already used with a different request",
            details={"code": "idempotency_key_reused", "resource": resource},
        )
    if outcome == "gone":
        raise AppException(
            code=ErrorCode.NOT_FOUND,
            status_code=410,
            message=f"The idempotent {resource} target no longer exists",
            details={"code": "idempotency_target_gone", "resource": resource},
        )
    if outcome == "invalid":
        raise AppException(
            code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            message="The idempotency request is invalid",
            details={"code": "idempotency_key_invalid", "resource": resource},
        )

