from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.platform.access.adapters.mcp_endpoint import resolver


class _Query:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _Supabase:
    def __init__(self, tables: dict[str, list[dict]]):
        self._tables = tables

    def table(self, name: str):
        return _Query(self._tables.get(name, []))


def test_resolver_builds_writable_context_when_scope_and_access_are_rw(monkeypatch):
    endpoint = {
        "id": "endpoint-1",
        "name": "Files",
        "project_id": "proj-1",
        "scope_id": "scope-1",
        "status": "active",
        "created_by": "user-1",
        "kind": "mcp",
    }
    sb = _Supabase(
        {
            "repository_scopes": [
                {
                    "id": "scope-1",
                    "project_id": "proj-1",
                    "path": "docs",
                    "exclude": ["private"],
                    "max_mode": "rw",
                }
            ],
        }
    )
    monkeypatch.setattr(resolver, "get_supabase_client", lambda: sb)
    monkeypatch.setattr(
        resolver,
        "_resolve_surface",
        lambda _key: (
            endpoint,
            {"fs_policy": {"accesses": [{"readonly": False}]}},
            {"id": "cred-1"},
        ),
    )

    with pytest.raises(HTTPException) as exc:
        resolver.resolve_mcp_scoped_fs_context("mcp_key")
    assert exc.value.status_code == 501
    assert exc.value.detail["code"] == "native_scope_not_available"


def test_resolver_downgrades_to_readonly_without_writable_access(monkeypatch):
    endpoint = {
        "id": "endpoint-1",
        "name": "Files",
        "project_id": "proj-1",
        "scope_id": "scope-1",
        "status": "active",
        "kind": "mcp",
    }
    sb = _Supabase(
        {
            "repository_scopes": [
                {
                    "id": "scope-1",
                    "project_id": "proj-1",
                    "path": "",
                    "exclude": [],
                    "max_mode": "rw",
                }
            ],
            "projects": [{"created_by": "project-owner"}],
        }
    )
    monkeypatch.setattr(resolver, "get_supabase_client", lambda: sb)
    monkeypatch.setattr(
        resolver,
        "_resolve_surface",
        lambda _key: (
            endpoint,
            {"fs_policy": {"accesses": [{"readonly": True}]}},
            {"id": "cred-1"},
        ),
    )

    with pytest.raises(HTTPException) as exc:
        resolver.resolve_mcp_scoped_fs_context("mcp_key")
    assert exc.value.status_code == 501
    assert exc.value.detail["code"] == "native_scope_not_available"


def test_resolver_applies_mcp_tools_config(monkeypatch):
    endpoint = {
        "id": "endpoint-1",
        "name": "Files",
        "project_id": "proj-1",
        "scope_id": "scope-1",
        "status": "active",
        "created_by": "user-1",
        "kind": "mcp",
    }
    sb = _Supabase(
        {
            "repository_scopes": [
                {
                    "id": "scope-1",
                    "project_id": "proj-1",
                    "path": "docs",
                    "exclude": [],
                    "max_mode": "rw",
                }
            ],
        }
    )
    monkeypatch.setattr(resolver, "get_supabase_client", lambda: sb)
    monkeypatch.setattr(
        resolver,
        "_resolve_surface",
        lambda _key: (
            endpoint,
            {
                "fs_policy": {"accesses": [{"readonly": False}]},
                "tools_policy": {"filesystem": {"allowed": ["fs_ls", "fs_rm"]}},
            },
            {"id": "cred-1"},
        ),
    )

    with pytest.raises(HTTPException) as exc:
        resolver.resolve_mcp_scoped_fs_context("mcp_key")
    assert exc.value.status_code == 501
    assert exc.value.detail["code"] == "native_scope_not_available"


def test_resolver_rejects_non_mcp_key():
    with pytest.raises(HTTPException) as exc:
        resolver.resolve_mcp_scoped_fs_context("cli_key")

    assert exc.value.status_code == 401


# ── GAP-4: carved child-scope exclusion for MCP keys ──────────────────


def test_merge_scope_excludes_carves_children_not_self_or_siblings():
    out = resolver._merge_scope_excludes(
        ["docs/secret.txt"],
        "docs",
        [{"path": "docs"}, {"path": "docs/api"}, {"path": "docs/api/v1"}, {"path": "other"}],
    )
    assert "docs/api" in out and "docs/api/v1" in out  # declared children carved
    assert "other" not in out and "docs" not in out  # sibling + self never carved
    assert "docs/secret.txt" in out  # user exclude preserved


def test_merge_scope_excludes_root_carves_nothing():
    # Root scope is the project-wide view — it sees all sub-scopes.
    assert resolver._merge_scope_excludes([], "", [{"path": "docs"}, {"path": "docs/api"}]) == []


def test_resolver_carves_child_scopes_for_parent_mcp_key(monkeypatch):
    # An MCP key bound to a NON-ROOT parent scope must hide declared child
    # scopes (GAP-4) — otherwise it could ls/cat/grep into their subtrees.
    endpoint = {
        "id": "endpoint-1",
        "name": "Files",
        "project_id": "proj-1",
        "scope_id": "scope-1",
        "status": "active",
        "created_by": "user-1",
        "kind": "mcp",
    }
    sb = _Supabase(
        {
            "repository_scopes": [
                {
                    "id": "scope-1",
                    "project_id": "proj-1",
                    "path": "docs",
                    "exclude": ["private"],
                    "max_mode": "rw",
                },
                {
                    "id": "scope-2",
                    "project_id": "proj-1",
                    "path": "docs/api",
                    "exclude": [],
                    "max_mode": "rw",
                },
                {
                    "id": "scope-3",
                    "project_id": "proj-1",
                    "path": "other",
                    "exclude": [],
                    "max_mode": "rw",
                },
            ],
        }
    )
    monkeypatch.setattr(resolver, "get_supabase_client", lambda: sb)
    monkeypatch.setattr(
        resolver,
        "_resolve_surface",
        lambda _key: (
            endpoint,
            {"fs_policy": {"accesses": [{"readonly": False}]}},
            {"id": "cred-1"},
        ),
    )

    with pytest.raises(HTTPException) as exc:
        resolver.resolve_mcp_scoped_fs_context("mcp_key")
    assert exc.value.status_code == 501
    assert exc.value.detail["code"] == "native_scope_not_available"


# --- Project-root MCP endpoints: native engine, owner's current grant ---------


def _root_endpoint(**overrides):
    endpoint = {
        "id": "endpoint-root",
        "name": "Project files",
        "project_id": "proj-1",
        "scope_id": None,
        "status": "active",
        "created_by": "owner-1",
        "kind": "mcp",
    }
    endpoint.update(overrides)
    return endpoint


def _grant(role: str):
    from src.platform.authorization.models import ROLE_CAPABILITIES, ProjectRole

    capabilities = ROLE_CAPABILITIES[ProjectRole(role)]
    return SimpleNamespace(allows=lambda action: action in capabilities)


def _wire(monkeypatch, endpoint, policy, grant, calls=None):
    monkeypatch.setattr(
        resolver, "_resolve_surface", lambda _key: (endpoint, policy, {"id": "cred-1"})
    )

    def owner_grant(project_id, owner):
        if calls is not None:
            calls.append((project_id, owner))
        if isinstance(grant, Exception):
            raise grant
        return grant

    monkeypatch.setattr(resolver, "_owner_grant", owner_grant)


WRITABLE = {"fs_policy": {"accesses": [{"path": "", "readonly": False}]}}


def test_project_root_endpoint_serves_the_owners_editor_authority(monkeypatch):
    calls = []
    _wire(monkeypatch, _root_endpoint(), WRITABLE, _grant("editor"), calls)
    runtime = resolver.resolve_mcp_runtime("mcp_key")
    ctx = runtime.context
    assert calls == [("proj-1", "owner-1")]  # Evaluated now, for this request.
    assert (ctx.project_id, ctx.scope_path, ctx.exclude, ctx.mode) == ("proj-1", "", [], "rw")
    assert ctx.actor == "owner-1"  # Writes are attributed to the delegating member.
    assert {"fs_write", "fs_ls"} <= ctx.allowed_tools


def test_a_viewer_owner_caps_a_writable_endpoint_to_read_only(monkeypatch):
    _wire(monkeypatch, _root_endpoint(), WRITABLE, _grant("viewer"))
    ctx = resolver.resolve_mcp_runtime("mcp_key").context
    assert ctx.mode == "ro"
    assert "fs_write" not in ctx.allowed_tools and "fs_ls" in ctx.allowed_tools


def test_a_read_only_policy_narrows_an_admin_owner(monkeypatch):
    policy = {"fs_policy": {"accesses": [{"path": "", "readonly": True}]}}
    _wire(monkeypatch, _root_endpoint(), policy, _grant("admin"))
    assert resolver.resolve_mcp_runtime("mcp_key").context.mode == "ro"


@pytest.mark.parametrize("owner", ["owner-1", None])
def test_an_owner_without_project_access_denies_the_key(monkeypatch, owner):
    _wire(monkeypatch, _root_endpoint(created_by=owner), WRITABLE, None)
    with pytest.raises(HTTPException) as exc:
        resolver.resolve_mcp_runtime("mcp_key")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "mcp_owner_access_revoked"


def test_authorization_storage_failure_is_not_a_denial_or_an_allow(monkeypatch):
    unavailable = HTTPException(
        status_code=503, detail={"code": "project_authorization_unavailable"}
    )
    _wire(monkeypatch, _root_endpoint(), WRITABLE, unavailable)
    with pytest.raises(HTTPException) as exc:
        resolver.resolve_mcp_runtime("mcp_key")
    assert exc.value.status_code == 503


def test_scope_bound_endpoints_still_wait_for_native_scope_views(monkeypatch):
    calls = []
    _wire(monkeypatch, _root_endpoint(scope_id="scope-1"), WRITABLE, _grant("admin"), calls)
    with pytest.raises(HTTPException) as exc:
        resolver.resolve_mcp_runtime("mcp_key")
    assert exc.value.status_code == 501 and calls == []


@pytest.mark.parametrize(
    ("role", "policy", "mode"), [("editor", WRITABLE, "rw"), ("viewer", WRITABLE, "r")]
)
def test_the_runtime_grant_binds_reads_and_publication_to_the_endpoint(
    monkeypatch, role, policy, mode
):
    from src.platform.repository_target.models import ProjectRootTarget

    _wire(monkeypatch, _root_endpoint(), policy, _grant(role))
    grant = resolver.resolve_mcp_runtime("mcp_key").grant
    assert grant.target == ProjectRootTarget(project_id="proj-1")
    assert grant.mode.value == mode and grant.repository_view.max_mode == "rw"
    assert (grant.principal.principal_id, grant.principal.credential_id) == (
        "endpoint-root",
        "cred-1",
    )
    assert grant.project_id == "proj-1"
