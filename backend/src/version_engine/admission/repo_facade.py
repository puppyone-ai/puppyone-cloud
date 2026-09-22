"""Repository facade facts for PuppyOne access surfaces.

PuppyOne exposes several repo-like entry points: project Git remotes,
Access Point Git remotes, and Access Point FS CLI commands. Externally
each one behaves like a small repository with its own auth, scope, ref, and
CAS boundary. Internally those facades share the project's Git object store
and publish through the same Write Engine.

This module is the narrow translation boundary between typed authorization
facts and repo-shaped projections. Protocol adapters consume ``RepoFacade``
instead of reconstructing target identity from dictionaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.platform.repository_target.auth_context import (
    repository_mode_from_auth,
    repository_view_from_auth,
)
from src.platform.repository_target.models import ResolvedRepositoryView
from src.version_engine.write_engine.path_utils import normalize_path
from src.utils.logger import log_warning

if TYPE_CHECKING:
    from src.version_engine.infrastructure.supabase.scope_manager import ScopeBackend


_WRITE_MODES = frozenset({"rw", "write", "w"})


class RepositoryViewResolutionError(RuntimeError):
    """A repository projection could not be resolved without widening access."""


@dataclass(frozen=True)
class RepoFacade:
    """A repo-shaped view over a project-shared object store."""

    project_id: str
    repo_id: str
    kind: str
    scope_path: str
    excludes: tuple[str, ...]
    mode: str
    ref: str = "refs/heads/main"
    object_store_scope: str = "project-shared"

    @property
    def read_only(self) -> bool:
        return self.mode not in _WRITE_MODES

    def audit_detail(self) -> dict:
        return {
            "repo_id": self.repo_id,
            "repo_kind": self.kind,
            "repo_ref": self.ref,
            "object_store_scope": self.object_store_scope,
            "scope": self.scope_path,
        }


def compute_carved_excludes(
    scope_path: str,
    all_scopes: list[dict],
) -> tuple[str, ...]:
    """Compute the set of child-scope paths that must be hidden from a parent scope.

    The V2 Star architecture requires that when a Scope A has
    sub-scopes B and C (at paths ``A/B`` and ``A/C``), the parent-scope view
    auto-excludes ``A/B`` and ``A/C`` so:
    - A parent Git push cannot accidentally write into a child scope's territory.
    - A parent scope view does not show content owned by a child scope.

    This is GAP-4 in the architecture gap analysis: without these auto-excludes
    the admission layer only enforces user-configured ``exclude`` lists, leaving
    cross-scope boundary violations silently allowed.

    PROJECT-ROOT EXCEPTION: the Project-root projection (``scope_path == ""``)
    is the full view — it intentionally does NOT carve out Scopes.
    Project root can read, write, and bidirectionally sync all Scope content; a
    Project-root write into a Scope's path projects into that Scope's
    head (and vice-versa) via the project-root visibility barrier. Sub-scope
    keys remain narrow (each sees only its own subtree), so this only widens
    Project-level credential — not any delegated Scope credential.

    Args:
        scope_path: The normalized path prefix ("" = Project root).
        all_scopes: All scopes for this project, each with at least {"path": str}.

    Returns:
        A tuple of normalized child-scope path strings to add as exclusions.
        Empty for the Project root.
    """
    clean = scope_path.strip("/")
    if not clean:
        # Project root = project-wide view: see/write/sync everything. Only
        # user-configured excludes apply (merged by the caller).
        return ()
    prefix = clean + "/"
    carved: list[str] = []
    for s in all_scopes:
        child_path = normalize_path(s.get("path", ""))
        if not child_path:
            continue
        # A Scope hides descendants whose path starts with "scope_path/".
        if child_path.startswith(prefix):
            carved.append(child_path)
    return tuple(sorted(set(carved)))


def repo_facade_from_auth(
    project_id: str,
    auth: dict,
    *,
    kind: str = "project_git_remote",
    scope_backend: "ScopeBackend | None" = None,
) -> RepoFacade:
    """Build the canonical repo facade from a resolved auth context.

    When ``scope_backend`` is supplied, auto-computes ``carved_excludes`` —
    the child-scope paths that must be hidden from this scope's view. This
    enforces the nested-scope isolation contract described in
    ``01-version-engine.md`` §"嵌套 Scope 拓扑":

        父 Scope 看不到 /A/C/*（已声明的子 scope 在父视图里自动隐藏）

    Canonical callers supply a fully resolved, immutable view and therefore do
    not need a backend lookup. The legacy Access Point compatibility route may
    still supply a backend; that lookup is fail-closed because silently
    omitting descendant exclusions would widen the credential's authority.
    """

    view = repository_view_from_auth(auth)
    if project_id and view.target.project_id != project_id:
        raise ValueError("repository view Project mismatch")
    return repository_view_to_facade(
        view,
        kind=kind,
        scope_backend=scope_backend,
        effective_mode=repository_mode_from_auth(auth),
        configured=auth.get("_repo_facade") or {},
        agent=str(auth.get("agent") or ""),
    )


def repository_view_to_facade(
    view: ResolvedRepositoryView,
    *,
    kind: str = "project_git_remote",
    scope_backend: "ScopeBackend | None" = None,
    effective_mode: str | None = None,
    configured: dict | None = None,
    agent: str = "",
) -> RepoFacade:
    """Translate one typed repository view into the Version Engine facade."""

    configured = configured or {}
    carved: tuple[str, ...] = ()
    if scope_backend is not None:
        try:
            list_scopes = getattr(
                scope_backend,
                "list_all_strict",
                scope_backend.list_all,
            )
            carved = compute_carved_excludes(
                view.path_prefix,
                list_scopes(),
            )
        except Exception as exc:  # noqa: BLE001 - translate at protocol boundary
            log_warning(
                f"[RepoFacade] carved excludes lookup failed for "
                f"project={view.target.project_id} path={view.path_prefix!r}: {exc}"
            )
            raise RepositoryViewResolutionError(
                "repository view could not be resolved"
            ) from exc
    excludes = tuple(dict.fromkeys(view.excludes + carved))
    scope_id = getattr(view.target, "scope_id", None)
    return RepoFacade(
        project_id=view.target.project_id,
        repo_id=str(
            configured.get("id")
            or scope_id
            or agent
            or f"{view.target.project_id}:root"
        ),
        kind=str(configured.get("kind") or kind),
        scope_path=normalize_path(view.path_prefix),
        excludes=excludes,
        mode=str(effective_mode or view.max_mode),
        ref=str(configured.get("ref") or view.ref),
        object_store_scope="project-shared",
    )
