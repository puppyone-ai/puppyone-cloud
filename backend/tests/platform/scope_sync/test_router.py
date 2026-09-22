"""HTTP wiring tests for the scope-sync policy endpoint."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.scope_sync.events import InMemoryEventStore
from src.platform.scope_sync.router import router
from src.platform.scope_sync.service import ScopeSyncService, get_scope_sync_service
from src.platform.scope_sync.settings_store import InMemorySettingsStore
from tests.authorization_fakes import authorization_for, install_authorization


@dataclass
class _Scope:
    id: str
    project_id: str
    path: str


_SCOPES = [_Scope("s1", "proj-1", "docs")]
_LOOKUP = {s.id: s for s in _SCOPES}


def _service() -> ScopeSyncService:
    return ScopeSyncService(
        scope_lookup=lambda sid: _LOOKUP.get(sid),
        scopes_lister=lambda pid: [(s.id, s.path) for s in _SCOPES if s.project_id == pid],
        event_store=InMemoryEventStore(),
        settings_store=InMemorySettingsStore(),
        scope_by_access_key=lambda k: _LOOKUP.get("s1") if k == "AKEY" else None,
    )


def _app(svc: ScopeSyncService | None = None) -> FastAPI:
    svc = svc or _service()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u1", email="u1@corp.com", role="authenticated", user_metadata={},
    )
    install_authorization(app, authorization_for("proj-1"))
    app.dependency_overrides[get_scope_sync_service] = lambda: svc
    return app


def test_policy_happy_path():
    client = TestClient(_app())
    resp = client.get("/api/v1/scope-sync/policy", params={
        "project_id": "proj-1", "scope_id": "s1", "persona": "non_dev",
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["persona"] == "non_dev" and data["scope_role"] == "sub"
    assert "checkpoint_debounce_s" in data["policy"]


def test_policy_forbidden_project_404():
    client = TestClient(_app())
    resp = client.get("/api/v1/scope-sync/policy", params={"project_id": "OTHER", "scope_id": "s1"})
    assert resp.status_code == 404


def test_policy_unknown_scope_404():
    client = TestClient(_app())
    resp = client.get("/api/v1/scope-sync/policy", params={"project_id": "proj-1", "scope_id": "ghost"})
    assert resp.status_code == 404


def test_events_endpoint_returns_fanned_out_events():
    svc = _service()
    svc.record_publish(project_id="proj-1", scope_path="docs", changed_paths=["a.md"],
                       head_version="v1", origin_user="someone-else")
    client = TestClient(_app(svc))
    resp = client.get("/api/v1/scope-sync/events", params={
        "project_id": "proj-1", "scope_id": "s1", "cursor": 0,
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["events"][0]["affected_paths"] == ["a.md"] and data["cursor"] >= 1


def test_ap_events_access_key_auth_no_jwt():
    svc = _service()
    svc.record_publish(project_id="proj-1", scope_path="docs", changed_paths=["a.md"],
                       head_version="v1", origin_user="other")
    app = FastAPI()
    app.include_router(router)
    # NOTE: deliberately NO get_current_user override — the sidecar uses access-key auth
    app.dependency_overrides[get_scope_sync_service] = lambda: svc
    client = TestClient(app)
    resp = client.get("/api/v1/scope-sync/ap/events", params={"cursor": 0},
                      headers={"X-Access-Key": "AKEY"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["scope_id"] == "s1" and data["events"][0]["affected_paths"] == ["a.md"]


def test_ap_events_requires_access_key():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_scope_sync_service] = lambda: _service()
    assert TestClient(app).get("/api/v1/scope-sync/ap/events").status_code == 401


def test_ap_events_bad_key_403():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_scope_sync_service] = lambda: _service()
    resp = TestClient(app).get("/api/v1/scope-sync/ap/events", headers={"X-Access-Key": "nope"})
    assert resp.status_code == 403


def test_stats_aggregates_recent_events():
    svc = _service()
    svc.record_publish(project_id="proj-1", scope_path="docs", changed_paths=["a.md"],
                       head_version="v1", origin_user="alice")
    svc.record_publish(project_id="proj-1", scope_path="docs", changed_paths=["b.md", "a.md"],
                       head_version="v2", origin_user="bob")
    resp = TestClient(_app(svc)).get("/api/v1/scope-sync/stats", params={
        "project_id": "proj-1", "scope_id": "s1"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["events_in_window"] >= 2
    assert data["distinct_origins"] == 2          # alice + bob
    assert data["distinct_paths"] == 2            # a.md + b.md
    assert data["latest_head"] == "v2"


def test_stats_forbidden_project_404():
    resp = TestClient(_app()).get("/api/v1/scope-sync/stats", params={
        "project_id": "OTHER", "scope_id": "s1"})
    assert resp.status_code == 404


def test_events_forbidden_project_404():
    resp = TestClient(_app()).get("/api/v1/scope-sync/events", params={
        "project_id": "OTHER", "scope_id": "s1"})
    assert resp.status_code == 404


def test_settings_put_then_get_roundtrip():
    svc = _service()
    client = TestClient(_app(svc))
    put = client.put("/api/v1/scope-sync/settings", json={
        "project_id": "proj-1", "scope_id": "s1", "persona": "non_dev", "auto_sync": False})
    assert put.status_code == 200 and put.json()["data"]["persona"] == "non_dev"
    got = client.get("/api/v1/scope-sync/settings", params={"project_id": "proj-1", "scope_id": "s1"})
    assert got.json()["data"] == {"persona": "non_dev", "auto_sync": False}


def test_settings_rejects_bad_persona():
    resp = TestClient(_app()).put("/api/v1/scope-sync/settings", json={
        "project_id": "proj-1", "scope_id": "s1", "persona": "wizard"})
    assert resp.status_code == 400
