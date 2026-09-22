"""Typed extraction helpers for Version Engine authorization contexts."""

from __future__ import annotations

from src.platform.authorization.models import ProjectAction, ProjectGrant, RuntimeGrant
from src.platform.repository_target.models import (
    RepositoryTarget,
    ResolvedRepositoryView,
)


class RepositoryAuthContextError(RuntimeError):
    """The admission boundary received no canonical repository view."""


def repository_view_from_auth(auth: dict) -> ResolvedRepositoryView:
    runtime_grant = auth.get("_runtime_grant")
    if isinstance(runtime_grant, RuntimeGrant):
        return runtime_grant.repository_view
    view = auth.get("_repository_view")
    if isinstance(view, ResolvedRepositoryView):
        return view
    raise RepositoryAuthContextError("canonical repository view is missing")


def repository_target_from_auth(auth: dict) -> RepositoryTarget:
    return repository_view_from_auth(auth).target


def repository_mode_from_auth(auth: dict) -> str:
    runtime_grant = auth.get("_runtime_grant")
    if isinstance(runtime_grant, RuntimeGrant):
        return runtime_grant.mode.value
    project_grant = auth.get("_project_grant")
    if isinstance(project_grant, ProjectGrant):
        return (
            "rw"
            if project_grant.allows(ProjectAction.CONTENT_WRITE)
            else "r"
        )
    return repository_view_from_auth(auth).max_mode
