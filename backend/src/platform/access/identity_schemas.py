"""Project connection identity request and response schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


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
