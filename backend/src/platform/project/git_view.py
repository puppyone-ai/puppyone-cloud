"""Human control-plane projection and repair for the root Git view.

The canonical ``/git/...`` routes are a machine data plane and accept only a
scope-bounded Git runtime credential.  This service gives authenticated human
Project surfaces the same derived health/repair operations without teaching
the Git transport to accept a JWT.
"""

from __future__ import annotations

from typing import Any

from src.platform.repository_target.models import ProjectRootTarget, ResolvedRepositoryView
from src.version_engine.adapters.git.health import git_view_health_payload
from src.version_engine.admission.repo_facade import RepoFacade, repository_view_to_facade
from src.version_engine.derived.git_transport_cache import rebuild_git_transport_view
from src.version_engine.infrastructure.supabase.repo_manager import VersionRepoManager


class ProjectGitViewService:
    """Expose root-view derived facts through the Human Project plane."""

    def __init__(
        self,
        repo_manager: VersionRepoManager,
    ) -> None:
        self._repo_manager = repo_manager

    def health(
        self,
        project_id: str,
        *,
        content_write_allowed: bool,
        cache_rebuild_allowed: bool,
    ) -> dict[str, Any]:
        repo, facade = self._root_view(project_id)
        payload = git_view_health_payload(
            repo,
            project_id=project_id,
            scope_path=facade.scope_path,
            scope_excludes=list(facade.excludes),
            read_only=facade.read_only or not content_write_allowed,
        )
        # Cache repair is a Project control-plane action, not ordinary content
        # write authority.  The UI uses this explicit fact instead of guessing
        # from role names or the Git view's read-only state.
        payload["can_rebuild"] = cache_rebuild_allowed
        return payload

    def rebuild(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        repo, facade = self._root_view(project_id)
        rebuilt_full = rebuild_git_transport_view(
            repo,
            scope_path=facade.scope_path,
            scope_excludes=list(facade.excludes),
            follow_history=True,
            include_blobs=True,
        )
        rebuilt_boundary = rebuild_git_transport_view(
            repo,
            scope_path=facade.scope_path,
            scope_excludes=list(facade.excludes),
            follow_history=False,
            include_blobs=False,
        )
        return {"variants": [rebuilt_full, rebuilt_boundary]}

    def _root_view(self, project_id: str) -> tuple[Any, RepoFacade]:
        repo = self._repo_manager.get_server_repo(project_id)
        facade = repository_view_to_facade(
            ResolvedRepositoryView(
                target=ProjectRootTarget(project_id=project_id),
                path_prefix="",
                excludes=(),
                max_mode="rw",
            ),
            kind="project_git_remote",
            scope_backend=self._repo_manager.get_scope_backend(project_id),
        )
        return repo, facade
