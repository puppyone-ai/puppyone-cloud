"""Canonical Project-root and repository-Scope target domain."""

from src.shared_types.repository_target import (
    ProjectRootTarget,
    RepositoryPathProjection,
    RepositoryTarget,
    RepositoryTargetKind,
    ResolvedRepositoryView,
    ScopeTarget,
    repository_target_from_storage,
    repository_target_scope_id,
)

__all__ = [
    "ProjectRootTarget",
    "RepositoryPathProjection",
    "RepositoryTarget",
    "RepositoryTargetKind",
    "ResolvedRepositoryView",
    "ScopeTarget",
    "repository_target_from_storage",
    "repository_target_scope_id",
]
