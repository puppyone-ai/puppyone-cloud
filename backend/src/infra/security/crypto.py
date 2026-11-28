"""Application-layer AES-256-GCM envelopes for stored secrets (db connectors, chat apps)."""

import base64
import json
import os
from typing import Any
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config import settings

_ENVELOPE_VERSION = 1
_ENVELOPE_ALGORITHM = "AES-256-GCM"
_ENVELOPE_ROOT = "_enc"
_MASKED_ROOT = "_masked"


def is_encrypted_db_connection_config(config: dict[str, Any]) -> bool:
    if not isinstance(config, dict):
        return False
    envelope = config.get(_ENVELOPE_ROOT)
    if not isinstance(envelope, dict):
        return False
    return all(k in envelope for k in ("v", "alg", "kid", "nonce", "ciphertext"))


def encrypt_secret(plain: bytes, *, key: bytes, kid: str) -> dict[str, Any]:
    """One versioned envelope; the caller owns which key and key id."""
    nonce = os.urandom(12)
    return {
        "v": _ENVELOPE_VERSION,
        "alg": _ENVELOPE_ALGORITHM,
        "kid": kid,
        "nonce": _b64encode(nonce),
        "ciphertext": _b64encode(AESGCM(key).encrypt(nonce, plain, None)),
    }


def decrypt_secret(envelope: dict[str, Any], *, key: bytes) -> bytes:
    if envelope.get("v") != _ENVELOPE_VERSION:
        raise ValueError("Unsupported secret envelope version")
    if envelope.get("alg") != _ENVELOPE_ALGORITHM:
        raise ValueError("Unsupported secret envelope algorithm")
    return AESGCM(key).decrypt(
        _b64decode(envelope["nonce"]), _b64decode(envelope["ciphertext"]), None
    )


def load_aes_key(encoded_key: str, name: str) -> bytes:
    encoded_key = encoded_key.strip()
    if not encoded_key:
        raise ValueError(f"{name} is required for encryption")
    try:
        key = base64.b64decode(encoded_key, validate=True)
    except Exception as exc:
        raise ValueError(f"{name} must be valid base64") from exc
    if len(key) != 32:
        raise ValueError(f"{name} must decode to 32 bytes")
    return key


def encrypt_db_connection_config(plain: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(plain, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return {
        _ENVELOPE_ROOT: encrypt_secret(
            payload, key=_load_encryption_key(), kid=settings.DB_CONNECTOR_ENCRYPTION_KID
        ),
        _MASKED_ROOT: mask_db_connection_config(plain),
    }


def decrypt_db_connection_config(stored: dict[str, Any]) -> dict[str, Any]:
    if not is_encrypted_db_connection_config(stored):
        return stored

    plaintext = decrypt_secret(stored[_ENVELOPE_ROOT], key=_load_encryption_key())
    payload = json.loads(plaintext.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Decrypted db_connection config must be an object")
    return payload


def mask_db_connection_config(plain: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_url": _mask_project_url(str(plain.get("project_url", ""))),
        "api_key": _mask_api_key(str(plain.get("api_key", ""))),
        "key_type": str(plain.get("key_type", "")),
    }


def _load_encryption_key() -> bytes:
    return load_aes_key(settings.DB_CONNECTOR_ENCRYPTION_KEY, "DB_CONNECTOR_ENCRYPTION_KEY")


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(raw: str) -> bytes:
    return base64.b64decode(raw.encode("ascii"))


def _mask_project_url(project_url: str) -> str:
    if not project_url:
        return ""
    parsed = urlparse(project_url)
    host = parsed.netloc or ""
    if host.endswith(".supabase.co"):
        project_ref = host[: -len(".supabase.co")]
        masked_ref = "***" if len(project_ref) <= 2 else f"{project_ref[:2]}***"
        scheme = parsed.scheme or "https"
        return f"{scheme}://{masked_ref}.supabase.co"
    if parsed.scheme and host:
        return f"{parsed.scheme}://***"
    return "***"


def _mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""

    tail = api_key[-4:] if len(api_key) >= 4 else ""
    if api_key.startswith("sb_publishable_"):
        return f"sb_publishable_***{tail}"
    if api_key.startswith("sb_secret_"):
        return f"sb_secret_***{tail}"

    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}***{tail}"
