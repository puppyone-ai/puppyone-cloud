from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REQUIRED_ENTITLEMENT_FEATURES = frozenset(
    {
        "access_surface.agent",
        "access_surface.mcp",
        "access_surface.sandbox",
        "automation.hosted",
        "remote_workspace.create",
        "scope_sandbox.connect",
    }
)
REQUIRED_ENTITLEMENT_LIMITS = frozenset(
    {
        "projects.max",
        "repo_scopes.max_per_project",
        "storage.max_bytes",
        "upload.max_single_file_bytes",
        "seats.purchased",
        "runtime.included_units",
    }
)
REQUIRED_ENTITLEMENT_ALLOW = frozenset({"access_surface_kinds"})


def validate_entitlement_values(
    entitlements: dict[str, Any],
    *,
    seat_quantity: int,
) -> None:
    """Validate the version-1 authorization payload without rejecting additive keys.

    Missing limits must never inherit the consumer's historical "unlimited"
    behavior. A catalog may still express an intentional unlimited value with
    an explicit JSON null.
    """

    for group in ("features", "limits", "allow"):
        if not isinstance(entitlements.get(group), dict):
            raise ValueError(f"entitlements.{group} must be an object")

    features = entitlements["features"]
    missing_features = REQUIRED_ENTITLEMENT_FEATURES - features.keys()
    if missing_features:
        raise ValueError(
            "entitlements.features is missing required keys: " + ", ".join(sorted(missing_features))
        )
    for key in REQUIRED_ENTITLEMENT_FEATURES:
        if not isinstance(features[key], bool):
            raise ValueError(f"entitlements.features.{key} must be a boolean")

    limits = entitlements["limits"]
    missing_limits = REQUIRED_ENTITLEMENT_LIMITS - limits.keys()
    if missing_limits:
        raise ValueError(
            "entitlements.limits is missing required keys: " + ", ".join(sorted(missing_limits))
        )
    for key in REQUIRED_ENTITLEMENT_LIMITS:
        value = limits[key]
        if value is None and key not in {"seats.purchased", "runtime.included_units"}:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(
                f"entitlements.limits.{key} must be a non-negative integer or an explicit null"
            )
    if limits["seats.purchased"] != seat_quantity:
        raise ValueError("entitlements.limits.seats.purchased must equal seat_quantity")
    storage_limit = limits["storage.max_bytes"]
    upload_limit = limits["upload.max_single_file_bytes"]
    if storage_limit is not None and upload_limit is not None and upload_limit > storage_limit:
        raise ValueError(
            "entitlements.limits.upload.max_single_file_bytes cannot exceed storage.max_bytes"
        )

    allow = entitlements["allow"]
    missing_allow = REQUIRED_ENTITLEMENT_ALLOW - allow.keys()
    if missing_allow:
        raise ValueError(
            "entitlements.allow is missing required keys: " + ", ".join(sorted(missing_allow))
        )
    access_surface_kinds = allow["access_surface_kinds"]
    if access_surface_kinds != "*":
        if not isinstance(access_surface_kinds, list) or not access_surface_kinds:
            raise ValueError(
                "entitlements.allow.access_surface_kinds must be '*' or a non-empty list"
            )
        if any(not isinstance(value, str) or not value for value in access_surface_kinds):
            raise ValueError(
                "entitlements.allow.access_surface_kinds values must be non-empty strings"
            )
        if len(access_surface_kinds) != len(set(access_surface_kinds)):
            raise ValueError("entitlements.allow.access_surface_kinds must not contain duplicates")


class EntitlementSnapshot(BaseModel):
    """Product-side projection of a PuppyPay entitlement publication.

    Database rows may gain additive columns over time, so read models ignore
    unknown fields. The inbound publication model below remains strict.
    """

    model_config = ConfigDict(extra="ignore")

    org_id: str
    schema_version: str = "1.0"
    plan_id: str = "free"
    status: str = "free"
    source: str = "local"
    entitlements: dict[str, Any] = Field(default_factory=dict)
    seat_quantity: int = Field(default=0, ge=0)
    catalog_version: str = "legacy"
    source_revision: int = Field(default=0, ge=0)
    effective_at: datetime | None = None
    current_period_end: datetime | None = None
    effective_until: datetime | None = None
    payload_hash: str = ""
    source_quote_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("payload_hash")
    @classmethod
    def validate_optional_payload_hash(cls, value: str) -> str:
        if value and (len(value) != 64 or any(char not in "0123456789abcdef" for char in value)):
            raise ValueError("payload_hash must be a lowercase sha256 digest")
        return value


class EntitlementUpsert(BaseModel):
    """Strict version-1 PuppyPay -> PuppyOne publication contract."""

    model_config = ConfigDict(extra="forbid")

    org_id: str = Field(min_length=1)
    schema_version: str
    plan_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    source: Literal["puppypay"]
    entitlements: dict[str, Any]
    seat_quantity: int = Field(ge=0)
    catalog_version: str = Field(min_length=1)
    source_revision: int = Field(gt=0)
    effective_at: datetime
    current_period_end: datetime | None = None
    effective_until: datetime | None = None
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_id: str | None = None
    source_quote_id: str | None = Field(default=None, min_length=1, max_length=255)
    event_type: str | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> EntitlementUpsert:
        try:
            major_text, separator, minor_text = self.schema_version.partition(".")
            if not separator or not major_text.isdigit() or not minor_text.isdigit():
                raise ValueError
            major = int(major_text)
            minor = int(minor_text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("invalid entitlement schema_version") from exc
        if major != 1:
            raise ValueError(f"unsupported entitlement schema major: {major}")
        if self.source_quote_id is not None and minor < 1:
            raise ValueError("source_quote_id requires entitlement schema_version 1.1 or newer")
        if self.catalog_version == "legacy":
            raise ValueError("PuppyPay publications require a non-legacy catalog_version")
        validate_entitlement_values(self.entitlements, seat_quantity=self.seat_quantity)
        return self


class EntitlementPublicationAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["inserted", "updated", "idempotent"]
    source_revision: int = Field(gt=0)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: EntitlementSnapshot
