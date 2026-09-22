"""Typed application and external Registry v1 contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.platform.project.schemas import ProjectOut

TEMPLATE_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
RELEASE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+@-]{0,127}$"
SHA256_PATTERN = r"^[a-fA-F0-9]{64}$"
TemplateTag = Annotated[str, Field(min_length=1, max_length=100)]
TemplatePath = Annotated[str, Field(min_length=1, max_length=512)]
TemplateMediaUrl = Annotated[str, Field(min_length=1, max_length=2048)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemplatePreviewNode(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    type: Literal["folder", "json", "markdown", "file"]


class TemplatePreviewDocument(StrictModel):
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=20_000)


class TemplateRelease(StrictModel):
    id: str = Field(pattern=RELEASE_ID_PATTERN)
    version: str = Field(min_length=1, max_length=128)
    bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    file_count: int = Field(ge=1, le=100_000)
    total_bytes: int = Field(ge=0, le=4 * 1024 * 1024 * 1024)
    published_at: datetime | None = None
    signing_key_id: str | None = Field(default=None, min_length=1, max_length=128)
    signature: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("bundle_sha256")
    @classmethod
    def normalize_digest(cls, value: str) -> str:
        return value.lower()


class TemplateSummary(StrictModel):
    id: str = Field(pattern=TEMPLATE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    icon: str = Field(default="📦", min_length=1, max_length=32)
    category: str | None = Field(default=None, max_length=100)
    cover_url: str | None = Field(default=None, max_length=2048)
    author: str | None = Field(default=None, max_length=200)
    tags: list[TemplateTag] = Field(default_factory=list, max_length=64)
    preview: list[TemplatePreviewNode] = Field(default_factory=list, max_length=12)
    current_release: TemplateRelease


class TemplateDetail(TemplateSummary):
    screenshots: list[TemplateMediaUrl] = Field(default_factory=list, max_length=12)
    long_description: str | None = Field(default=None, max_length=100_000)
    file_tree: list[TemplatePath] = Field(default_factory=list, max_length=5000)
    preview_document: TemplatePreviewDocument | None = None
    releases: list[TemplateRelease] = Field(default_factory=list, max_length=100)


class TemplateRegistryStatus(StrictModel):
    mode: Literal["disabled", "builtin", "remote"]
    catalog_enabled: bool
    instantiation_enabled: bool
    source: Literal["disabled", "builtin", "remote"]
    reason: str | None = Field(default=None, max_length=200)


class TemplateCatalog(StrictModel):
    registry: TemplateRegistryStatus
    templates: list[TemplateSummary] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, max_length=1024)


class RemoteTemplateCatalog(StrictModel):
    """Wire payload returned by an external Registry's ``GET /v1/templates``."""

    templates: list[TemplateSummary]
    next_cursor: str | None = Field(default=None, max_length=1024)


class TemplateInstantiationRequest(StrictModel):
    org_id: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    release_id: str | None = Field(default=None, pattern=RELEASE_ID_PATTERN)

    @field_validator("org_id")
    @classmethod
    def normalize_org_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("org_id must not be blank")
        return normalized


class TemplateInstantiation(StrictModel):
    template_id: str = Field(pattern=TEMPLATE_ID_PATTERN)
    release_id: str = Field(pattern=RELEASE_ID_PATTERN)
    project: ProjectOut


class TemplateBundleFile(StrictModel):
    path: str = Field(min_length=1, max_length=4096)
    size: int = Field(ge=0, le=4 * 1024 * 1024 * 1024)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("sha256")
    @classmethod
    def normalize_digest(cls, value: str) -> str:
        return value.lower()


class TemplateBundleManifest(StrictModel):
    format_version: Literal[1]
    template_id: str = Field(pattern=TEMPLATE_ID_PATTERN)
    release_id: str = Field(pattern=RELEASE_ID_PATTERN)
    created_at: datetime | None = None
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    files: list[TemplateBundleFile] = Field(min_length=1, max_length=100_000)

    @field_validator("content_sha256")
    @classmethod
    def normalize_digest(cls, value: str) -> str:
        return value.lower()
