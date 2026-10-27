"""Pause/resume invalidation must address the same target as admission."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.platform.access.service import _clear_connector_admission_cache
from src.platform.authorization.models import RuntimeGrant, RuntimeMode, RuntimePrincipal
from src.platform.repository_target.models import (
    ProjectRootTarget,
    ResolvedRepositoryView,
    ScopeTarget,
)
from src.version_engine.admission import channel_pause


def _auth(project_id="project-1", scope_id=None):
    target = (
        ProjectRootTarget(project_id=project_id)
        if scope_id is None
        else ScopeTarget(project_id=project_id, scope_id=scope_id)
    )
    return {
        "_runtime_grant": RuntimeGrant(
            principal=RuntimePrincipal(
                principal_id="credential-1", credential_kind="git_http_token"
            ),
            target=target,
            repository_view=ResolvedRepositoryView(
                target=target, path_prefix="docs" if scope_id else "", excludes=(), max_mode="rw"
            ),
            mode=RuntimeMode.READ_WRITE,
        )
    }


@pytest.fixture(autouse=True)
def clean_cache():
    channel_pause.clear_channel_pause_cache()
    yield
    channel_pause.clear_channel_pause_cache()


@pytest.mark.parametrize("scope_id", [None, "scope-1"])
@pytest.mark.parametrize("channel", ["cli", "git_remote", "mcp"])
def test_management_invalidation_observes_pause_and_resume(monkeypatch, scope_id, channel):
    surface = SimpleNamespace(id="surface-1", status="active")
    calls = []

    class Repo:
        def get_by_target_kind(self, project_id, target_scope_id, kind):
            calls.append((project_id, target_scope_id, kind))
            return surface

    monkeypatch.setattr(channel_pause, "AccessModelRepository", Repo)
    auth = _auth(scope_id=scope_id)
    channel_pause.enforce_channel_pause(auth, channel)
    channel_pause.enforce_channel_pause(auth, channel)
    assert len(calls) == 1

    surface.status = "paused"
    _clear_connector_admission_cache(scope_id, channel)
    with pytest.raises(HTTPException) as error:
        channel_pause.enforce_channel_pause(auth, channel)
    assert error.value.status_code == 403

    surface.status = "active"
    _clear_connector_admission_cache(scope_id, channel)
    channel_pause.enforce_channel_pause(auth, channel)
    assert calls == [("project-1", scope_id, channel)] * 3


@pytest.mark.parametrize("channel_filter", [None, "git_remote"])
def test_scope_invalidation_preserves_other_targets(monkeypatch, channel_filter):
    calls = []

    class Repo:
        def get_by_target_kind(self, project_id, scope_id, channel):
            calls.append((project_id, scope_id, channel))
            return SimpleNamespace(id="surface-1", status="active")

    monkeypatch.setattr(channel_pause, "AccessModelRepository", Repo)
    targets = [_auth(), _auth("project-2"), _auth(scope_id="scope-1"), _auth(scope_id="scope-2")]
    for auth in targets:
        for channel in ("git_remote", "cli"):
            channel_pause.enforce_channel_pause(auth, channel)
    assert len(calls) == 8
    calls.clear()

    channel_pause.clear_channel_pause_cache(scope_id="scope-1", channel=channel_filter)
    for auth in targets:
        for channel in ("git_remote", "cli"):
            channel_pause.enforce_channel_pause(auth, channel)
    expected = [("project-1", "scope-1", "git_remote")]
    if channel_filter is None:
        expected.append(("project-1", "scope-1", "cli"))
    assert calls == expected
