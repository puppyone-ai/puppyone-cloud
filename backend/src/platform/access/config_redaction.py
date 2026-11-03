"""Keep credentials out of anything Access config returns or accepts."""

from __future__ import annotations

import re
from typing import Any

# Config keys that hold machine credentials / secrets. These must never be
# returned by ordinary list/detail/mutation responses. Raw values are only
# meaningful in the one-time create/regenerate issuance responses.
_SECRET_CONFIG_KEYS = {
    "access_key",
    "mcp_api_key",
    "api_key",
    "secret",
    "client_secret",
    "token",
    "bearer_token",
    "refresh_token",
    "access_token",
    "password",
    "private_key",
    "credential",
    "credentials",
}

_SECRET_CONFIG_KEY_SUFFIXES = (
    "_access_key",
    "_api_key",
    "_secret",
    "_password",
    "_private_key",
    "_bearer_token",
    "_access_token",
    "_refresh_token",
    "_credential",
    "_credentials",
)


def _normalise_config_key(key: Any) -> str:
    """Return a conservative snake-case-ish representation for secret checks."""
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower()
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def _is_secret_config_key(key: Any) -> bool:
    normalised = _normalise_config_key(key)
    return normalised in _SECRET_CONFIG_KEYS or normalised.endswith(_SECRET_CONFIG_KEY_SUFFIXES)


def redact_config(value: Any) -> Any:
    """Recursively remove credential-bearing fields from response config.

    Access surface config is intentionally provider-extensible, so a shallow
    blacklist is unsafe: a provider can nest credentials under OAuth/session
    metadata and ordinary read or mutation responses would echo them. Keep the
    non-secret shape for existing clients, but fail closed for known credential
    names at every depth (including camelCase variants and provider prefixes).
    """
    if isinstance(value, dict):
        return {
            key: redact_config(child)
            for key, child in value.items()
            if not _is_secret_config_key(key)
        }
    if isinstance(value, list):
        return [redact_config(child) for child in value]
    if isinstance(value, tuple):
        return tuple(redact_config(child) for child in value)
    return value


def contains_secret_config_key(value: Any) -> bool:
    """Reject attempts to smuggle credentials through the metadata endpoint."""
    if isinstance(value, dict):
        return any(
            _is_secret_config_key(key) or contains_secret_config_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list | tuple):
        return any(contains_secret_config_key(child) for child in value)
    return False
