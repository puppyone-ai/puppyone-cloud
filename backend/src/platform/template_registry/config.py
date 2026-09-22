"""Template Registry and portable bundle configuration."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RegistryMode = Literal["disabled", "builtin", "remote"]


class TemplateRegistrySettings(BaseSettings):
    """Operator-owned Registry selection and import trust policy.

    Built-in mode preserves the open-source starter templates without coupling
    the application to the separately hosted official Registry.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
        env_file_encoding="utf-8",
    )

    TEMPLATE_REGISTRY_MODE: RegistryMode = "builtin"
    TEMPLATE_REGISTRY_URL: str = ""
    TEMPLATE_REGISTRY_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0, le=60)
    TEMPLATE_REGISTRY_CACHE_TTL_SECONDS: float = Field(default=60.0, ge=0, le=3600)
    TEMPLATE_REGISTRY_REQUIRE_SIGNATURE: bool = True
    TEMPLATE_REGISTRY_TRUSTED_KEYS_JSON: str = "{}"
    TEMPLATE_REGISTRY_MAX_METADATA_BYTES: int = Field(
        default=5 * 1024 * 1024,
        ge=64 * 1024,
        le=50 * 1024 * 1024,
    )

    TEMPLATE_BUNDLE_MAX_COMPRESSED_BYTES: int = Field(
        default=50 * 1024 * 1024, ge=1024, le=2 * 1024 * 1024 * 1024
    )
    TEMPLATE_BUNDLE_MAX_EXPANDED_BYTES: int = Field(
        default=250 * 1024 * 1024, ge=1024, le=4 * 1024 * 1024 * 1024
    )
    TEMPLATE_BUNDLE_MAX_FILE_BYTES: int = Field(
        default=50 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024
    )
    TEMPLATE_BUNDLE_MAX_FILES: int = Field(default=2000, ge=1, le=100_000)
    # Version Engine currently accepts at most 500 characters. Keep the
    # Registry ceiling at or below that write-path boundary so a verified
    # release cannot fail only after its destination Project was created.
    TEMPLATE_BUNDLE_MAX_PATH_LENGTH: int = Field(default=255, ge=32, le=500)
    TEMPLATE_BUNDLE_MAX_PATH_DEPTH: int = Field(default=32, ge=1, le=256)

    @model_validator(mode="after")
    def validate_remote_configuration(self) -> TemplateRegistrySettings:
        if self.TEMPLATE_REGISTRY_MODE != "remote":
            return self

        value = self.TEMPLATE_REGISTRY_URL.strip()
        if not value:
            raise ValueError("TEMPLATE_REGISTRY_URL is required in remote mode")
        parsed = urlsplit(value)
        is_local_http = parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        if parsed.scheme != "https" and not is_local_http:
            raise ValueError("remote Template Registry must use HTTPS (HTTP is local-only)")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("TEMPLATE_REGISTRY_URL must be an absolute URL without credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("TEMPLATE_REGISTRY_URL cannot contain a query or fragment")
        # Treat malformed trust configuration as a deployment error instead of
        # discovering it halfway through an instantiate request.
        self.trusted_public_keys()
        return self

    def trusted_public_keys(self) -> dict[str, bytes]:
        """Return validated ``{key_id: raw Ed25519 public key}`` bytes."""

        try:
            value = json.loads(self.TEMPLATE_REGISTRY_TRUSTED_KEYS_JSON or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("TEMPLATE_REGISTRY_TRUSTED_KEYS_JSON must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("TEMPLATE_REGISTRY_TRUSTED_KEYS_JSON must be a JSON object")

        keys: dict[str, bytes] = {}
        for key_id, encoded in value.items():
            if not isinstance(key_id, str) or not key_id.strip():
                raise ValueError("trusted Registry key IDs must be non-empty strings")
            if not isinstance(encoded, str):
                raise ValueError(f"trusted Registry key {key_id!r} must be base64 text")
            try:
                padded = encoded + "=" * (-len(encoded) % 4)
                raw = base64.b64decode(
                    padded.encode("ascii"),
                    altchars=b"-_",
                    validate=True,
                )
            except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
                raise ValueError(f"trusted Registry key {key_id!r} is not valid base64") from exc
            if len(raw) != 32:
                raise ValueError(f"trusted Registry key {key_id!r} must be 32 bytes")
            keys[key_id] = raw
        return keys


template_registry_settings = TemplateRegistrySettings()
