from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LANDING_CONTRACT_VERSION = 1


class PreviewInfo(BaseModel):
    filename: str
    tool_kind: str
    content_chars: int = Field(..., description="Length of the parsed markdown")
    excerpt: str = Field(..., description="Leading slice of the parsed content")
    suggested_tools: list[str] = Field(
        default_factory=list,
        description="MCP tools the eventual endpoint will expose (for the preview panel)",
    )


class PreviewResponse(BaseModel):
    contract_version: Literal[1] = LANDING_CONTRACT_VERSION
    ticket: str = Field(..., description="Signed capability ticket to present at /landing/claim")
    preview: PreviewInfo
    expires_at: int = Field(..., description="Unix epoch seconds when the ticket/preview expires")


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[1] = LANDING_CONTRACT_VERSION
    ticket: str = Field(min_length=1, max_length=8192)
    org_id: str = Field(min_length=1, max_length=128)

    @field_validator("org_id")
    @classmethod
    def normalize_org_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("org_id must not be blank")
        return normalized


class ClaimMcp(BaseModel):
    server_url: str
    api_key: str
    endpoint_id: str


class ClaimResponse(BaseModel):
    contract_version: Literal[1] = LANDING_CONTRACT_VERSION
    project_id: str
    repo: str = Field(..., description="Scope/folder path the content lives under")
    mcp: ClaimMcp
    deep_link: str = Field(..., description="Relative path into the app for the new repo")
