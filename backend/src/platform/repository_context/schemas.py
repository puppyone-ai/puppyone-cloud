from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.platform.repository_context.models import GitCredentialMode
from src.platform.repository_target.schemas import RepositoryTargetSchema


class GitRemoteOut(BaseModel):
    url: str
    target: RepositoryTargetSchema
    username: str = "x-puppyone-token"


class GitCredentialIssueRequest(BaseModel):
    target: RepositoryTargetSchema
    mode: GitCredentialMode = GitCredentialMode.READ_WRITE
    credential: str = Field(pattern=r"^pwg_[A-Za-z0-9_-]{43}$")


class GitCredentialOut(BaseModel):
    id: str
    mode: GitCredentialMode
    remote: GitRemoteOut


class GitCredentialRevocationOut(BaseModel):
    id: str
    revoked: bool = True


class RepositoryContextResolveRequest(BaseModel):
    target: RepositoryTargetSchema


class RepositoryProjectSummaryOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    org_id: str
    visibility: str
    bound_git_branch: str
    updated_at: str | None = None
    effective_role: Literal["admin", "editor", "viewer"]
    grant_source: Literal["org_owner", "project_member", "org_visibility"]
    capabilities: list[str]


class RepositoryProjectContextOut(BaseModel):
    target: RepositoryTargetSchema
    project: RepositoryProjectSummaryOut
    scope_path: str | None = None
