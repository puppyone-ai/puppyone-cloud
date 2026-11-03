"""Declarative route to an OpenAI-compatible model provider.

A profile names its API key's environment variable and never holds the key.
Settings validate profiles at startup, so a bad route refuses to boot instead of
failing customer requests.
"""

import json
import re
from collections.abc import Sequence
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

DEFAULT_ACCOUNT_REF = "managed-default"
DEFAULT_CACHED_TOKENS_PATH = "prompt_tokens_details.cached_tokens"

# Held reservations recover against this route, so configuration cannot replace it.
RESERVED_ROUTES = frozenset({("openrouter", DEFAULT_ACCOUNT_REF)})

# Changing these would alter what executes or how it is metered.
_PROTECTED_FIELDS = frozenset(
    {
        "content",
        "messages",
        "model",
        "role",
        "stream",
        "stream_options",
        "tool_call_id",
        "tool_calls",
    }
)
_CREDENTIAL_HEADERS = frozenset(
    {"api-key", "authorization", "cookie", "proxy-authorization", "x-api-key"}
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_USAGE_PATH = re.compile(r"^[a-zA-Z0-9_]+(\.[a-zA-Z0-9_]+)*$")


class ManagedAIProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    provider: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,32}$")
    account_ref: str = Field(default=DEFAULT_ACCOUNT_REF, pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    base_url: str
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    headers: dict[str, str] = Field(default_factory=dict)
    # Dotted path inside the stream's `usage`, or 0 for a provider with no prompt
    # cache. Absent is never read as zero: that would remove a customer discount.
    cached_tokens: str | Literal[0] = DEFAULT_CACHED_TOKENS_PATH
    # Removed from the request and from each message, for provider dialects.
    drop_fields: tuple[str, ...] = ()
    # Renames top-level request fields, e.g. max_tokens -> max_completion_tokens.
    rename_fields: dict[str, str] = Field(default_factory=dict)

    @property
    def route(self) -> tuple[str, str]:
        return (self.provider, self.account_ref)

    @field_validator("base_url")
    @classmethod
    def https_or_loopback(cls, value: str) -> str:
        parts = urlsplit(value)
        loopback = parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
        if not (parts.scheme == "https" or loopback) or not parts.hostname:
            raise ValueError("base_url must use HTTPS")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("base_url cannot carry credentials, a query or a fragment")
        return value.rstrip("/")

    @field_validator("headers")
    @classmethod
    def no_credential_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if any(name.lower() in _CREDENTIAL_HEADERS for name in value):
            raise ValueError("Credentials belong in api_key_env, not headers")
        return value

    @field_validator("cached_tokens")
    @classmethod
    def usage_path(cls, value: str | int) -> str | int:
        if isinstance(value, str) and not _USAGE_PATH.fullmatch(value):
            raise ValueError("cached_tokens must be a dotted usage path or 0")
        return value

    @model_validator(mode="after")
    def metering_fields_stay_intact(self):
        changed = {*self.drop_fields, *self.rename_fields, *self.rename_fields.values()}
        if changed & _PROTECTED_FIELDS:
            raise ValueError("Execution and metering fields cannot be dropped or renamed")
        return self


_PROFILES = TypeAdapter(tuple[ManagedAIProviderProfile, ...])


def parse_provider_profiles(raw: str | Sequence | None) -> tuple[ManagedAIProviderProfile, ...]:
    """Read and validate configured routes; blank means none are configured."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("MANAGED_AI_PROVIDERS must be a JSON array") from exc
    if isinstance(raw, dict | str) or not isinstance(raw, Sequence):
        raise ValueError("MANAGED_AI_PROVIDERS must be a JSON array")
    profiles = _PROFILES.validate_python(tuple(raw))
    routes = [profile.route for profile in profiles]
    if len(set(routes)) != len(routes):
        raise ValueError("MANAGED_AI_PROVIDERS repeats a provider route")
    if RESERVED_ROUTES.intersection(routes):
        raise ValueError("MANAGED_AI_PROVIDERS cannot replace the built-in OpenRouter route")
    return profiles
