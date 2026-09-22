from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

OfficeStatus = Literal[
    "ready",
    "editing",
    "saving",
    "saved",
    "awaiting-save",
    "closed",
    "error",
]


@dataclass(frozen=True, slots=True)
class OfficeSessionRecord:
    session_id: str
    owner_user_id: str
    document_key: str
    title: str
    extension: str
    source_key: str
    result_key: str
    status: OfficeStatus
    result_revision: int
    result_sha256: str
    message: str
    created_at_ms: int
    updated_at_ms: int
    expires_at_ms: int

    def with_updates(self, **updates) -> OfficeSessionRecord:
        return replace(self, **updates)


class OfficeAvailabilityResponse(BaseModel):
    available: bool
    engine: Literal["onlyoffice"] = "onlyoffice"
    reason: str | None = None


class OfficeEditorSessionResponse(BaseModel):
    session_id: str
    api_script_url: str
    editor_config: dict
    status: OfficeStatus
    result_revision: int = 0
    expires_at: str


class OfficeSessionStateResponse(BaseModel):
    session_id: str
    status: OfficeStatus
    result_revision: int
    message: str | None = None
    expires_at: str


class OfficeCallbackBody(BaseModel):
    key: str
    status: int
    url: str | None = None
    filetype: str | None = None


class OfficeForceSaveResponse(BaseModel):
    accepted: bool = True


class OfficeCloseResponse(BaseModel):
    closed: bool = True


class OnlyOfficeUser(BaseModel):
    id: str
    name: str = Field(max_length=128)


def iso_from_millis(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace("+00:00", "Z")

