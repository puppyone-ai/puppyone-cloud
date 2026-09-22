"""Pydantic request/response DTOs for the repo redesign module.

Naming convention:
    *In   — incoming (request body)
    *Out  — outgoing (response body)
    *Patch — partial update body (all fields optional)

Routers translate Domain models (models.py) ↔ these DTOs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from src.platform.repository_target.schemas import RepositoryTargetSchema


# ──────────────────────────────────────────────────────────────────────────
# Scopes
# ──────────────────────────────────────────────────────────────────────────

ModeLiteral = Literal["r", "rw"]
DirectionLiteral = Literal["bidirectional", "inbound", "outbound"]
RoleLiteral = Literal["admin", "editor", "reader", "denied"]


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


# ──────────────────────────────────────────────────────────────────────────
# Repo identity (the project's URL + prompt template)
# ──────────────────────────────────────────────────────────────────────────


class RepoIdentityScopeOut(BaseModel):
    """A scope summary embedded in the identity payload — just enough to
    render the per-scope connect URL/key block on /access."""

    id: str
    name: str
    path: str
    git_url: str


class RepoIdentityOut(BaseModel):
    project_id: str
    url: str                              # canonical credential-free root Git URL
    prompt_template: str
    scopes: list[RepoIdentityScopeOut]
    content_initialized: bool = False
    head_commit_id: Optional[str] = None


class RepoIdentityPatch(BaseModel):
    prompt_template: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────
# Connectors
# ──────────────────────────────────────────────────────────────────────────


class TriggerSpec(BaseModel):
    type: Literal["manual", "scheduled", "on_change"] = "manual"
    config: Optional[dict[str, Any]] = None


class ConnectorIn(BaseModel):
    target: RepositoryTargetSchema
    provider: str = Field(..., min_length=1, max_length=64)        # service rejects 'cli' and 'agent' (auto-only)
    direction: DirectionLiteral
    name: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    oauth_connection_id: Optional[int] = None
    trigger: TriggerSpec = Field(default_factory=TriggerSpec)


class TargetAccessEnableIn(BaseModel):
    """Explicitly enable the standard machine entry points for one target."""

    target: RepositoryTargetSchema


class ConnectorPatch(BaseModel):
    name: Optional[str] = None
    direction: Optional[DirectionLiteral] = None
    config: Optional[dict[str, Any]] = None
    policy: Optional[dict[str, Any]] = None
    oauth_connection_id: Optional[int] = None
    trigger: Optional[TriggerSpec] = None
    status: Optional[Literal["active", "paused"]] = None        # explicit pause/resume goes through dedicated endpoints


class ConnectorOut(BaseModel):
    id: str
    target: RepositoryTargetSchema
    provider: str
    name: str
    direction: DirectionLiteral
    config: dict[str, Any]
    policy: dict[str, Any]
    oauth_connection_id: Optional[int]
    trigger: dict[str, Any]
    status: str
    last_run_at: Optional[datetime]
    last_run_id: Optional[str]
    error_message: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime


class ConnectorRunOut(BaseModel):
    id: str
    connector_id: str
    status: str                          # 'running' | 'success' | 'failed'
    started_at: datetime
    finished_at: Optional[datetime]
    duration_ms: Optional[int]
    error: Optional[str]


# ──────────────────────────────────────────────────────────────────────────
# Permissions (team plans)
# ──────────────────────────────────────────────────────────────────────────


class PermissionIn(BaseModel):
    user_id: str
    role: RoleLiteral
    allowed_scope_ids: Optional[list[str]] = None     # None means "all scopes"


class PermissionPatch(BaseModel):
    role: Optional[RoleLiteral] = None
    allowed_scope_ids: Optional[list[str]] = None


class PermissionOut(BaseModel):
    project_id: str
    user_id: str
    role: RoleLiteral
    source: Literal["explicit", "inherited_org", "no_org_member"]
    allowed_scope_ids: Optional[list[str]]
    granted_by: Optional[str]
    granted_at: Optional[datetime]


class PermissionCheckIn(BaseModel):
    user_id: str
    action: Literal["read", "write", "admin"]
    scope_id: Optional[str] = None


class PermissionCheckOut(BaseModel):
    allowed: bool
    reason: str
