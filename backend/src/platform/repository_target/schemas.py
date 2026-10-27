"""Wire contract for canonical repository targets."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

from src.platform.repository_target.models import (
    ProjectRootTarget,
    RepositoryTarget,
    ScopeTarget,
)


class ProjectRootTargetSchema(BaseModel):
    kind: Literal["project_root"] = "project_root"
    project_id: str


class ScopeTargetSchema(BaseModel):
    kind: Literal["scope"] = "scope"
    project_id: str
    scope_id: str


RepositoryTargetSchema = Annotated[
    ProjectRootTargetSchema | ScopeTargetSchema,
    Field(discriminator="kind"),
]


def repository_target_schema(target: RepositoryTarget) -> RepositoryTargetSchema:
    if isinstance(target, ProjectRootTarget):
        return ProjectRootTargetSchema(project_id=target.project_id)
    if isinstance(target, ScopeTarget):
        return ScopeTargetSchema(
            project_id=target.project_id,
            scope_id=target.scope_id,
        )
    raise TypeError(f"Unsupported repository target: {type(target)!r}")


def repository_target_domain(target: RepositoryTargetSchema) -> RepositoryTarget:
    if isinstance(target, ProjectRootTargetSchema):
        return ProjectRootTarget(project_id=target.project_id)
    return ScopeTarget(project_id=target.project_id, scope_id=target.scope_id)


ModeLiteral = Literal["r", "rw"]


class ScopeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    path: str = Field(..., min_length=1, max_length=512)
    exclude: list[str] = Field(default_factory=list)
    max_mode: ModeLiteral = "rw"


class ScopePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    exclude: Optional[list[str]] = None
    max_mode: Optional[ModeLiteral] = None


class ScopeOut(BaseModel):
    id: str
    project_id: str
    name: str
    path: str
    exclude: list[str]
    max_mode: ModeLiteral
    created_at: datetime
    updated_at: datetime


class ScopeAutoSuggestOut(BaseModel):
    """Returned by POST /scopes/auto-suggest — proposed scopes the user can
    accept individually."""

    suggestions: list[ScopeIn]
