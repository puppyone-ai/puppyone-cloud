"""Unit tests for nested-scope carved_excludes (GAP-4 fix).

Verifies that compute_carved_excludes correctly enumerates child-scope
paths that must be hidden from a parent scope, and that repo_facade_from_auth
merges user-configured excludes with auto-carved ones when a scope_backend
is supplied.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.version_engine.admission.repo_facade import (
    RepositoryViewResolutionError,
    compute_carved_excludes,
    repo_facade_from_auth,
)
from src.platform.repository_target.models import (
    ProjectRootTarget,
    ResolvedRepositoryView,
    ScopeTarget,
)


ALL_SCOPES = [
    {"path": "", "id": "root", "mode": "rw", "exclude": []},
    {"path": "docs", "id": "docs", "mode": "rw", "exclude": []},
    {"path": "docs/api", "id": "docs-api", "mode": "r", "exclude": []},
    {"path": "src", "id": "src", "mode": "rw", "exclude": []},
    {"path": "src/lib", "id": "src-lib", "mode": "rw", "exclude": []},
]


class TestComputeCarvedExcludes:
    def test_root_scope_carves_nothing(self):
        # Root is the project-wide view: it sees/writes/syncs ALL sub-scopes,
        # so it auto-carves NOTHING (only user-configured excludes apply).
        carved = compute_carved_excludes("", ALL_SCOPES)
        assert carved == ()

    def test_docs_scope_excludes_only_its_children(self):
        carved = compute_carved_excludes("docs", ALL_SCOPES)
        assert "docs/api" in carved
        # siblings / cousins must not be hidden
        assert "src" not in carved
        assert "src/lib" not in carved
        assert "docs" not in carved  # self never carved

    def test_src_scope_excludes_src_lib(self):
        carved = compute_carved_excludes("src", ALL_SCOPES)
        assert "src/lib" in carved
        assert "docs" not in carved
        assert "docs/api" not in carved

    def test_leaf_scope_has_no_carved_children(self):
        carved = compute_carved_excludes("docs/api", ALL_SCOPES)
        assert carved == ()

    def test_empty_all_scopes(self):
        assert compute_carved_excludes("", []) == ()
        assert compute_carved_excludes("docs", []) == ()

    def test_no_self_in_carved(self):
        """The current scope path must never appear in its own exclusion set."""
        for scope in ALL_SCOPES:
            path = scope["path"]
            carved = compute_carved_excludes(path, ALL_SCOPES)
            assert path not in carved, f"self-exclusion found for scope={path!r}"

    def test_result_is_sorted_and_deduped(self):
        carved = compute_carved_excludes("docs", ALL_SCOPES)
        assert list(carved) == sorted(set(carved))


class TestRepoFacadeCarvedExcludes:
    def _make_auth(self, scope_path: str = "", user_exclude: list[str] | None = None) -> dict:
        target = (
            ScopeTarget(project_id="proj", scope_id="test-scope")
            if scope_path
            else ProjectRootTarget(project_id="proj")
        )
        return {
            "_repository_view": ResolvedRepositoryView(
                target=target,
                path_prefix=scope_path,
                excludes=tuple(user_exclude or ()),
                max_mode="rw",
            )
        }

    def _make_backend(self, all_scopes=None) -> MagicMock:
        backend = MagicMock()
        backend.list_all.return_value = all_scopes if all_scopes is not None else ALL_SCOPES
        backend.list_all_strict.return_value = (
            all_scopes if all_scopes is not None else ALL_SCOPES
        )
        return backend

    def test_no_scope_backend_uses_only_user_excludes(self):
        auth = self._make_auth(scope_path="docs", user_exclude=["docs/private"])
        facade = repo_facade_from_auth("proj", auth, kind="access_point", scope_backend=None)
        assert "docs/private" in facade.excludes
        assert "docs/api" not in facade.excludes

    def test_scope_backend_adds_carved_excludes(self):
        auth = self._make_auth(scope_path="docs")
        facade = repo_facade_from_auth("proj", auth, kind="access_point",
                                       scope_backend=self._make_backend())
        assert "docs/api" in facade.excludes

    def test_carved_and_user_excludes_merged_no_dups(self):
        # user already manually listed docs/api — should not appear twice
        auth = self._make_auth(scope_path="docs", user_exclude=["docs/api"])
        facade = repo_facade_from_auth("proj", auth, kind="access_point",
                                       scope_backend=self._make_backend())
        assert facade.excludes.count("docs/api") == 1

    def test_root_scope_exposes_all_child_scopes(self):
        # Root no longer carves sub-scopes — it is the full project view.
        # Only user-configured excludes (none here) should appear.
        auth = self._make_auth(scope_path="")
        facade = repo_facade_from_auth("proj", auth, kind="access_point",
                                       scope_backend=self._make_backend())
        for name in ("docs", "docs/api", "src", "src/lib"):
            assert name not in facade.excludes, f"root must not carve {name}"

    def test_project_root_cannot_carry_scope_exclusions(self):
        with pytest.raises(ValueError, match="Project root view"):
            self._make_auth(scope_path="", user_exclude=["secrets"])

    def test_scope_backend_failure_fails_closed(self):
        """A DB error must never widen the target by dropping child excludes."""
        backend = MagicMock()
        backend.list_all.side_effect = RuntimeError("supabase hiccup")
        backend.list_all_strict.side_effect = RuntimeError("supabase hiccup")
        auth = self._make_auth(scope_path="docs", user_exclude=["docs/private"])
        with pytest.raises(RepositoryViewResolutionError):
            repo_facade_from_auth(
                "proj", auth, kind="access_point", scope_backend=backend
            )
