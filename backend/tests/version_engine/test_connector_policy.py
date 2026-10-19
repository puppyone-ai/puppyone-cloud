from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.platform.authorization.models import RuntimeGrant, RuntimeMode, RuntimePrincipal
from src.platform.repository_target.models import (
    ProjectRootTarget,
    ResolvedRepositoryView,
    ScopeTarget,
)
from src.version_engine.admission import connector_policy


def _auth(scope_id: str | None = "scope-1", project_id: str = "project-1") -> dict:
    target = (
        ScopeTarget(project_id=project_id, scope_id=scope_id)
        if scope_id is not None
        else ProjectRootTarget(project_id=project_id)
    )
    return {
        "agent": f"scope:{scope_id}",
        "_runtime_grant": RuntimeGrant(
            principal=RuntimePrincipal(
                principal_id="credential-1",
                credential_kind="legacy_access_key",
            ),
            target=target,
            repository_view=ResolvedRepositoryView(
                target=target,
                path_prefix="docs" if scope_id is not None else "",
                excludes=(),
                max_mode="rw",
            ),
            mode=RuntimeMode.READ_WRITE,
        ),
    }


def test_missing_cli_fs_policy_uses_default_without_delete():
    allowed = connector_policy.effective_cli_fs_allowed_commands({})

    assert "ls" in allowed
    assert "write" in allowed
    assert "rm" not in allowed
    assert "rmdir" not in allowed


def test_explicit_cli_fs_policy_is_exact_allow_list():
    allowed = connector_policy.effective_cli_fs_allowed_commands(
        {
            "fs": {"allowed_commands": ["ls", "cat", "unknown", "rm"]},
        }
    )

    assert allowed == frozenset({"ls", "cat", "rm"})


@pytest.mark.parametrize("scope_id", [None, "scope-1"])
def test_cli_fs_command_policy_allows_cached_command(monkeypatch, scope_id):
    calls = []

    class _Repo:
        def get_by_target_kind(self, project_id, scope_id, provider):
            calls.append((project_id, scope_id, provider))
            return SimpleNamespace(
                id="connector-1",
                kind="cli",
                status="active",
                policy={"fs": {"allowed_commands": ["ls"]}},
            )

    connector_policy.clear_connector_policy_cache()
    monkeypatch.setattr(connector_policy, "AccessModelRepository", _Repo)

    connector_policy.admit_cli_fs_command(_auth(scope_id), "ls", "cli")
    connector_policy.admit_cli_fs_command(_auth(scope_id), "ls", "cli")

    assert calls == [("project-1", scope_id, "cli")]


@pytest.mark.parametrize("scope_id", [None, "scope-1"])
def test_cli_fs_command_policy_denies_unlisted_command(monkeypatch, scope_id):
    class _Repo:
        def get_by_target_kind(self, _project_id, _scope_id, _provider):
            return SimpleNamespace(
                id="connector-1",
                kind="cli",
                status="active",
                policy={"fs": {"allowed_commands": ["ls"]}},
            )

    connector_policy.clear_connector_policy_cache()
    monkeypatch.setattr(connector_policy, "AccessModelRepository", _Repo)

    with pytest.raises(HTTPException) as exc:
        connector_policy.admit_cli_fs_command(_auth(scope_id), "rm", "cli")

    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "CLI_FS_COMMAND_DENIED"
    assert exc.value.detail["command"] == "rm"


@pytest.mark.parametrize("scope_id", [None, "scope-1"])
def test_cli_fs_command_policy_fails_closed_on_lookup_error(monkeypatch, scope_id):
    class _Repo:
        def get_by_target_kind(self, _project_id, _scope_id, _provider):
            raise RuntimeError("db down")

    connector_policy.clear_connector_policy_cache()
    monkeypatch.setattr(connector_policy, "AccessModelRepository", _Repo)

    with pytest.raises(HTTPException) as exc:
        connector_policy.admit_cli_fs_command(_auth(scope_id), "ls", "cli")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Connector policy could not be verified"


@pytest.mark.parametrize("scope_id", [None, "scope-1"])
@pytest.mark.parametrize("status", [None, "paused"])
def test_cli_fs_requires_configured_active_target_surface(monkeypatch, scope_id, status):
    class _Repo:
        def get_by_target_kind(self, _project_id, _scope_id, _provider):
            return (
                SimpleNamespace(id="connector-1", kind="cli", status=status, policy={})
                if status is not None
                else None
            )

    connector_policy.clear_connector_policy_cache()
    monkeypatch.setattr(connector_policy, "AccessModelRepository", _Repo)
    with pytest.raises(HTTPException) as exc:
        connector_policy.admit_cli_fs_command(_auth(scope_id), "cat", "cli")
    assert exc.value.status_code == 403


def test_project_root_policy_cache_isolated_by_project_and_scope(monkeypatch):
    calls = []

    class _Repo:
        def get_by_target_kind(self, project_id, scope_id, provider):
            calls.append((project_id, scope_id, provider))
            return SimpleNamespace(
                id=f"{project_id}:{scope_id}",
                kind="cli",
                status="active",
                policy={
                    "fs": {
                        "allowed_commands": ["cat"]
                        if project_id == "project-1" and scope_id is None
                        else []
                    }
                },
            )

    connector_policy.clear_connector_policy_cache()
    monkeypatch.setattr(connector_policy, "AccessModelRepository", _Repo)
    connector_policy.admit_cli_fs_command(_auth(None), "cat", "cli")
    for auth in [_auth(None, "project-2"), _auth("scope-1")]:
        with pytest.raises(HTTPException) as exc:
            connector_policy.admit_cli_fs_command(auth, "cat", "cli")
        assert exc.value.detail["error_code"] == "CLI_FS_COMMAND_DENIED"
    assert len(calls) == 3

    # AccessService invalidates by kind for Project-root policy/pause updates.
    connector_policy.clear_connector_policy_cache(scope_id=None, provider="cli")
    connector_policy.admit_cli_fs_command(_auth(None), "cat", "cli")
    assert len(calls) == 4
