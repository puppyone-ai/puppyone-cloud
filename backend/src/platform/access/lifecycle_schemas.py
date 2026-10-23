"""Typed lifecycle requests. Raw credentials are excluded from representations."""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from src.platform.authorization.models import AccessCapability, AccessChannel


class AccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _secret(value: SecretStr, prefix: str) -> SecretStr:
    if not re.fullmatch(re.escape(prefix) + r"_[A-Za-z0-9_-]{43,128}", value.get_secret_value()):
        raise ValueError("Invalid credential format")
    return value


class RegistrationCreate(AccessRequest):
    id: UUID
    name: str = Field(min_length=1, max_length=120)
    owner_org_id: str | None = Field(default=None, min_length=1, max_length=128)
    secret: SecretStr

    @field_validator("secret")
    @classmethod
    def check_secret(cls, value):
        return _secret(value, "par")


class StateChange(AccessRequest):
    state: Literal["active", "suspended", "revoked"]


class RegistrationKeyReset(AccessRequest):
    secret: SecretStr

    @field_validator("secret")
    @classmethod
    def check_secret(cls, value):
        return _secret(value, "par")


class InvitationCreate(AccessRequest):
    id: UUID
    secret: SecretStr
    target_kind: Literal["project_root", "workspace"]
    workbench_id: UUID | None = None
    capabilities: list[AccessCapability] = Field(min_length=1, max_length=5)
    channels: list[AccessChannel] = Field(min_length=1, max_length=4)
    recipient_user_id: UUID | None = None
    max_grant_seconds: int | None = Field(default=None, ge=60, le=31536000)
    expires_at: datetime
    max_approvals: int = Field(default=1, ge=1, le=1000)

    @field_validator("secret")
    @classmethod
    def check_secret(cls, value):
        return _secret(value, "pai")

    @model_validator(mode="after")
    def validate_target(self):
        from src.platform.authorization.service import validate_access_shape

        if (self.target_kind == "workspace") != (self.workbench_id is not None):
            raise ValueError("Workspace targets require an exact workspace identity")
        validate_access_shape(self.target_kind, self.capabilities, self.channels)
        if self.expires_at.tzinfo is None:
            raise ValueError("Expiry must include a timezone")
        return self


class AccessApplication(AccessRequest):
    id: UUID
    registration_id: UUID
    invitation: SecretStr

    @field_validator("invitation")
    @classmethod
    def check_secret(cls, value):
        return _secret(value, "pai")


class AccessDecision(AccessRequest):
    decision: Literal["approve", "reject"]
    capabilities: list[AccessCapability] | None = Field(default=None, min_length=1, max_length=5)
    channels: list[AccessChannel] | None = Field(default=None, min_length=1, max_length=4)
    expires_at: datetime | None = None
    single_session: bool = False

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Expiry must include a timezone")
        return value


class CredentialRenewal(AccessRequest):
    request_id: UUID
    grant_id: UUID
    channel: AccessChannel
    next_secret: SecretStr
    credential_secret: SecretStr | None = None
    public_key: str | None = Field(default=None, min_length=40, max_length=8192)
    surface_id: str | None = Field(default=None, min_length=1, max_length=128)
    expires_in: int = Field(default=900, ge=60, le=3600)

    @field_validator("next_secret")
    @classmethod
    def check_secret(cls, value):
        return _secret(value, "par")

    @model_validator(mode="after")
    def check_protocol(self):
        if self.channel == AccessChannel.SSH:
            if (
                self.public_key is None
                or self.credential_secret is not None
                or self.surface_id is not None
            ):
                raise ValueError("SSH requires a public key, without repository credentials")
        else:
            if (
                self.credential_secret is None
                or self.surface_id is None
                or self.public_key is not None
            ):
                raise ValueError("Repository access requires its Surface and credential")
            _secret(self.credential_secret, "mcp" if self.channel == AccessChannel.MCP else "git")
        return self
