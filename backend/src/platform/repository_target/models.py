"""Repository target identity and resolved-view facts.

Project is the canonical repository root. A repository Scope is an optional
path boundary within that same object store and history; it is never a second
repository and never a human authorization grant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RepositoryTargetKind(StrEnum):
    PROJECT_ROOT = "project_root"
    SCOPE = "scope"


@dataclass(frozen=True, slots=True)
class ProjectRootTarget:
    project_id: str
    kind: RepositoryTargetKind = RepositoryTargetKind.PROJECT_ROOT

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id is required")


@dataclass(frozen=True, slots=True)
class ScopeTarget:
    project_id: str
    scope_id: str
    kind: RepositoryTargetKind = RepositoryTargetKind.SCOPE

    def __post_init__(self) -> None:
        if not self.project_id or not self.scope_id:
            raise ValueError("project_id and scope_id are required")


type RepositoryTarget = ProjectRootTarget | ScopeTarget


def repository_target_from_storage(
    project_id: str,
    scope_id: str | None,
) -> RepositoryTarget:
    """The sole nullable-FK to domain-union mapping boundary."""

    if scope_id is None:
        return ProjectRootTarget(project_id=project_id)
    return ScopeTarget(project_id=project_id, scope_id=scope_id)


def repository_target_scope_id(target: RepositoryTarget) -> str | None:
    return target.scope_id if isinstance(target, ScopeTarget) else None


@dataclass(frozen=True, slots=True)
class ResolvedRepositoryView:
    """Current projection facts consumed by Version Engine admission."""

    target: RepositoryTarget
    path_prefix: str
    excludes: tuple[str, ...]
    max_mode: str
    ref: str = "refs/heads/main"

    def __post_init__(self) -> None:
        if self.max_mode not in {"r", "rw"}:
            raise ValueError("max_mode must be 'r' or 'rw'")
        if isinstance(self.target, ProjectRootTarget):
            if self.path_prefix or self.excludes or self.max_mode != "rw":
                raise ValueError(
                    "Project root view must be unprefixed, unexcluded, and rw-capable"
                )
        elif not self.path_prefix:
            raise ValueError("Scope view requires a non-empty path_prefix")

    def as_scope_projection(self) -> dict[str, object]:
        """Version Engine algorithm input, without identity semantics."""

        return {
            "path": self.path_prefix,
            "exclude": list(self.excludes),
            "mode": self.max_mode,
        }


@dataclass(frozen=True, slots=True)
class RepositoryPathProjection:
    """Path-bounded operational view layered beneath a repository target.

    Hosted Agent/Sandbox batch jobs may deliberately operate on a folder
    beneath a Project root. That path is execution policy, not Scope identity,
    so it must not be represented by a synthetic ``ScopeTarget``.
    """

    path_prefix: str = ""
    excludes: tuple[str, ...] = ()
    mode: str = "rw"

    def __post_init__(self) -> None:
        if self.mode not in {"r", "rw"}:
            raise ValueError("mode must be 'r' or 'rw'")

    def as_engine_projection(self) -> dict[str, object]:
        return {
            "path": self.path_prefix,
            "exclude": list(self.excludes),
            "mode": self.mode,
        }
