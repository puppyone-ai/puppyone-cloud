"""Boundaries every Version API request crosses in get_version_auth."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from src.config import settings
from src.platform.access import authenticator
from src.shared_types.grants import RuntimeGrant


def _credential(project_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        project_id=project_id,
        credential_id="credential-1",
        credential_type="access_key",
        access_surface_id="surface-1",
        scope=SimpleNamespace(
            id="scope-1",
            project_id=project_id,
            path="docs",
            exclude=[],
            max_mode="r",
        ),
    )


@pytest.fixture
def machine_caller(monkeypatch):
    monkeypatch.setattr(settings, "SKIP_AUTH", False)
    monkeypatch.setattr(authenticator.PuppyOneAuthenticator, "_try_jwt", lambda _self, _token: None)


def _authenticate_key_of(monkeypatch, credential_project: str, requested_project: str) -> dict:
    monkeypatch.setattr(
        authenticator,
        "resolve_scope_access_credential",
        lambda _supabase, _key: _credential(credential_project),
    )
    return authenticator.PuppyOneAuthenticator(Mock()).authenticate("key", requested_project)


@pytest.mark.usefixtures("machine_caller")
def test_an_access_key_opens_its_own_project(monkeypatch):
    auth = _authenticate_key_of(monkeypatch, "project-a", "project-a")

    assert isinstance(auth["_runtime_grant"], RuntimeGrant)
    assert auth["_runtime_grant"].target.project_id == "project-a"


@pytest.mark.usefixtures("machine_caller")
def test_an_access_key_cannot_open_another_project(monkeypatch):
    with pytest.raises(HTTPException) as refused:
        _authenticate_key_of(monkeypatch, "project-b", "project-a")

    assert refused.value.status_code == 401


def _version_auth(monkeypatch, enforce) -> dict:
    monkeypatch.setattr(authenticator, "SupabaseClient", Mock)
    monkeypatch.setattr(
        authenticator.PuppyOneAuthenticator,
        "authenticate",
        lambda _self, _token, _project, user_identity="": {"agent": "scope:1"},
    )
    monkeypatch.setattr(authenticator, "enforce_channel_pause", enforce)
    request = SimpleNamespace(headers={"x-puppy-client": "cli"})
    return authenticator.get_version_auth(request, "project-a", SimpleNamespace(credentials="key"))


def test_an_open_channel_returns_the_authenticated_context(monkeypatch):
    checked = []

    def enforce(auth, channel, *, log_prefix):
        checked.append((auth, channel))

    auth = _version_auth(monkeypatch, enforce)

    assert auth == {"agent": "scope:1"}
    assert checked == [({"agent": "scope:1"}, "cli")]


def test_a_paused_channel_stops_the_request_after_authentication(monkeypatch):
    def enforce(_auth, _channel, *, log_prefix):
        raise HTTPException(status_code=403, detail="paused")

    with pytest.raises(HTTPException) as refused:
        _version_auth(monkeypatch, enforce)

    assert refused.value.status_code == 403
