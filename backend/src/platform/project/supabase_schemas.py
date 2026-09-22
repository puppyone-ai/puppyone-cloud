"""
Project Data Models

Defines Pydantic models corresponding to the project table, used for type checking and data validation.
"""

import secrets
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


def generate_project_share_token() -> str:
    """Generate the legacy per-project share token required by projects."""
    return f"prj_{secrets.token_urlsafe(24)}"


class ProjectBase(BaseModel):
    """Project base model"""

    name: str
    description: str | None = None
    org_id: str | None = None
    visibility: str = "org"
    bound_git_branch: str = "main"
    created_by: str | None = None
    # Per the share-link MVP: every project has a URL-safe token; whoever
    # has the token can join (default: viewer). Rotation invalidates
    # outstanding links. See ``20260524000000_project_share_token.sql``.
    share_token: str | None = None


class ProjectCreate(ProjectBase):
    """Create project model"""

    id: str | None = None
    share_token: str = Field(default_factory=generate_project_share_token)


class ProjectUpdate(BaseModel):
    """Update project model"""

    name: str | None = None
    description: str | None = None
    visibility: str | None = None
    bound_git_branch: str | None = None
    share_token: str | None = None


class ProjectResponse(ProjectBase):
    """Project response model"""

    id: str
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
