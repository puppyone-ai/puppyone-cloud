from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Literal

CapabilityPurpose = Literal["source", "callback"]


def issue_capability(
    *,
    session_id: str,
    purpose: CapabilityPurpose,
    secret: str,
    ttl_seconds: int,
    now_seconds: int | None = None,
) -> str:
    now_value = int(time.time()) if now_seconds is None else now_seconds
    payload = {
        "exp": now_value + ttl_seconds,
        "purpose": purpose,
        "session_id": session_id,
        "v": 1,
    }
    encoded = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_capability(
    token: str,
    *,
    session_id: str,
    purpose: CapabilityPurpose,
    secret: str,
    now_seconds: int | None = None,
) -> bool:
    try:
        encoded, provided = token.split(".", 1)
        expected = _encode(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(provided, expected):
            return False
        payload = json.loads(_decode(encoded))
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
    now_value = int(time.time()) if now_seconds is None else now_seconds
    return bool(
        payload.get("v") == 1
        and payload.get("session_id") == session_id
        and payload.get("purpose") == purpose
        and isinstance(payload.get("exp"), int)
        and payload["exp"] >= now_value
    )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

