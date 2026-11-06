"""Real SQL/PostgREST through the HTTP boundary; verified principal is injected.

This proves application composition, not Supabase's already independent login.
"""
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient

from src.config import settings
from src.exception_handler import app_exception_handler
from src.exceptions import AppException
from src.platform.auth.dependencies import get_current_user, get_initialization_service
from src.platform.auth.initialization import UserInitializationService
from src.platform.auth.models import CurrentUser
from src.platform.onboarding.router import router as onboarding_router
from src.platform.organization.dependencies import get_org_service
from src.platform.organization.repository import OrganizationRepository
from src.platform.organization.router import router as organization_router
from src.platform.organization.service import OrganizationService
from src.platform.profile.repository import ProfileRepositorySupabase
from tests.agent.runtime.owned_http import owned_http_service


def test_personal_space_http_composition_and_team_guards(database, monkeypatch):
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "static")
    with owned_http_service("postgrest", database_url=database.url) as url, httpx.Client(trust_env=False) as transport:
        pg = SyncPostgrestClient(url, http_client=transport)
        client = SimpleNamespace(table=pg.from_, rpc=pg.rpc)
        repo = OrganizationRepository(client)
        initialization = UserInitializationService(ProfileRepositorySupabase(client), repo)
        orgs = OrganizationService(repo)
        app = FastAPI()
        app.include_router(onboarding_router)
        app.include_router(organization_router)
        app.add_exception_handler(AppException, app_exception_handler)
        actor = {"id": database.owner}
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=actor["id"], email="owner@example.test", role="authenticated")
        app.dependency_overrides[get_initialization_service] = lambda: initialization
        app.dependency_overrides[get_org_service] = lambda: orgs
        with TestClient(app) as api:
            first = api.post("/auth/personal-space", json={"user_id": database.colleague})
            assert first.status_code == 200, first.text
            personal = first.json()["data"]
            assert personal["kind"] == "personal" and personal["can_create_projects"]
            assert repo.get_by_id(personal["id"]).personal_owner_id == database.owner
            assert api.post("/auth/personal-space").json()["data"]["id"] == personal["id"]
            listing = api.get("/organizations/").json()["data"]
            assert {row["id"]: row["kind"] for row in listing} == {database.team: "team", personal["id"]: "personal"}
            assert api.delete(f"/organizations/{personal['id']}").status_code == 403
            assert api.post(f"/organizations/{personal['id']}/invite", json={"email": "colleague@example.test", "role": "member"}).status_code == 403
            actor["id"] = database.colleague
            other = api.post("/auth/personal-space")
            assert other.status_code == 200, other.text
            assert other.json()["data"]["id"] != personal["id"]
            assert personal["id"] not in {row["id"] for row in api.get("/organizations/").json()["data"]}
        assert database.sql(f"SELECT default_org_id FROM profiles WHERE user_id='{database.owner}'") == database.team
