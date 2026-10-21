"""Short-lived signed tokens for `/download`.

Why this exists:
    The download endpoint must be hittable by a plain `<a href download>`,
    which means the browser opens it as a top-level navigation and we
    can't attach an `Authorization: Bearer ...` header. We could use
    cookies, but PuppyOne is bearer-token based across the rest of the
    API and we don't want to start mixing auth schemes.

    Instead, the frontend exchanges its bearer token for a one-shot,
    short-lived signed URL via `POST /download/sign`. The download
    endpoint then validates the token from the query string before
    streaming the bytes. This is the same pattern S3/CloudFront/GCS
    presigned URLs use.

Properties of the token:
    * Bound to (project_id, path, user_id) — can't be replayed for a
      different file or by a different user
    * 5-minute TTL — long enough to start the download, short enough
      that a leaked URL is mostly harmless
    * Stateless (HMAC over `JWT_SECRET`) — no DB writes, no cache
      invalidation, scales horizontally for free
    * URL-safe base64 — drops cleanly into a query parameter

Format: `<payload_b64>.<signature_b64>`
    payload_b64  = urlsafe_b64(json({"pid","p","uid","exp"}))
    signature_b64 = urlsafe_b64(HMAC-SHA256(secret, payload_b64))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from src.config import settings

DEFAULT_TTL_SECONDS = 300  # 5 minutes — covers slow networks, short enough to be safe


@dataclass(frozen=True)
class DownloadTokenClaims:
    project_id: str
    path: str
    user_id: str
    expires_at: int  # unix seconds


class DownloadTokenError(Exception):
    """Raised when a token is malformed, tampered, or expired."""


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64_decode(s: str) -> bytes:
    # Restore padding stripped by `_b64_encode`. urlsafe_b64decode is
    # strict about length being a multiple of 4.
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _signing_key() -> bytes:
    """Derive a stable signing key from JWT_SECRET.

    We deliberately don't reuse JWT_SECRET directly so that download
    tokens and API JWTs live in separate keyspaces — even if one
    pipeline ever leaks an oracle, it can't be coerced into producing
    valid artifacts for the other.
    """
    return hashlib.sha256(b"download-token:" + settings.JWT_SECRET.encode("utf-8")).digest()


def issue_token(
    project_id: str,
    path: str,
    user_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[str, int]:
    """Mint a token for `(project_id, path, user_id)`.

    Returns `(token, expires_at_unix_seconds)`.
    """
    expires_at = int(time.time()) + ttl_seconds
    payload = {
        "pid": project_id,
        "p": path,
        "uid": user_id,
        "exp": expires_at,
    }
    payload_bytes = json.dumps(
        payload, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    payload_b64 = _b64_encode(payload_bytes)

    signature = hmac.new(
        _signing_key(), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    sig_b64 = _b64_encode(signature)

    return f"{payload_b64}.{sig_b64}", expires_at


def verify_token(token: str) -> DownloadTokenClaims:
    """Validate `token` and return its claims.

    Raises `DownloadTokenError` for any of:
      * structurally malformed token
      * tampered payload (signature mismatch)
      * expired token
    """
    if not token or "." not in token:
        raise DownloadTokenError("malformed token")

    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError as exc:
        raise DownloadTokenError("malformed token") from exc

    expected_sig = hmac.new(
        _signing_key(), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()

    try:
        provided_sig = _b64_decode(sig_b64)
    except (ValueError, base64.binascii.Error) as exc:
        raise DownloadTokenError("malformed signature") from exc

    # Constant-time compare to keep timing oracles off the table.
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise DownloadTokenError("invalid signature")

    try:
        payload = json.loads(_b64_decode(payload_b64))
    except (ValueError, base64.binascii.Error) as exc:
        raise DownloadTokenError("malformed payload") from exc

    try:
        claims = DownloadTokenClaims(
            project_id=payload["pid"],
            path=payload["p"],
            user_id=payload["uid"],
            expires_at=int(payload["exp"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DownloadTokenError("missing or invalid claim") from exc

    if claims.expires_at < int(time.time()):
        raise DownloadTokenError("token expired")

    return claims
