"""Strict Pydantic schema for repository write and backup policy profiles."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.platform.repository_policy.channels import WriteChannel


class WritesPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    workspace: bool
    initialization: bool
    git_push: bool
    content_api: bool
    mcp: bool
    cli: bool
    upload: bool
    imports: bool = Field(alias="import")
    sync: bool
    table: bool
    restore: bool
    head: bool

    def allows(self, channel: WriteChannel) -> bool:
        if channel is WriteChannel.IMPORT:
            return self.imports
        return bool(getattr(self, channel.value))


class BackupPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    target: Literal["project", "workspace"]
    on_pause: bool
    interval_seconds: int = Field(..., ge=0)


class RepositoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    writes: WritesPolicy
    backup: BackupPolicy

    def allows_write(self, channel: WriteChannel) -> bool:
        return self.writes.allows(channel)
