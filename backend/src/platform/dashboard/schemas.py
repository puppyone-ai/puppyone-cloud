"""Dashboard response. Every resource carries its domain, so equal IDs never merge."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from src.platform.access.public_schemas import AccessKind
from src.platform.repository_target.schemas import RepositoryTargetSchema


class DashboardProject(BaseModel):
    id: str
    name: str
    description: str | None = None


class DashboardNodeCounts(BaseModel):
    total: int = 0
    folders: int = 0
    files: int = 0


class DashboardTool(BaseModel):
    id: str
    name: str
    type: str | None = None


class DashboardUpload(BaseModel):
    id: str
    status: str
    type: str
    progress: int = 0
    message: str | None = None


class DashboardResourceSummary(BaseModel):
    resource_id: str
    project_id: str
    name: str | None = None
    path: str | None = None
    direction: str | None = None
    status: str
    trigger: dict | None = None
    last_activity_at: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    usage_buckets: list[int] = Field(default_factory=list)


class DashboardSynchronizeBinding(DashboardResourceSummary):
    resource_kind: Literal["synchronize"] = "synchronize"
    provider: str
    path: str


class DashboardAccessSurface(DashboardResourceSummary):
    resource_kind: Literal["access"] = "access"
    kind: AccessKind
    target: RepositoryTargetSchema
    scope_mode: Literal["r", "rw"] | None = None


DashboardResource = Annotated[
    DashboardSynchronizeBinding | DashboardAccessSurface, Field(discriminator="resource_kind")
]


class ResourceDashboard(BaseModel):
    project: DashboardProject
    nodes: DashboardNodeCounts
    resources: list[DashboardResource]
    tools: list[DashboardTool]
    uploads: list[DashboardUpload]
