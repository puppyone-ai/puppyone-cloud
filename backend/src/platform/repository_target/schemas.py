"""Wire contract for canonical repository targets."""

from __future__ import annotations

from typing import Annotated, Literal

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
