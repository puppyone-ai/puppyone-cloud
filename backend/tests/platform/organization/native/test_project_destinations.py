"""Where a new Project may go: what Desktop is told, against what creation admits.

Desktop offers a space as a destination when /organizations/ says
``can_create_projects``; creation itself is admitted in SQL. These tests hold the
two to the same answer for every role, and show that a refused creation leaves
nothing behind. Real PostgreSQL and PostgREST; the verified principal is injected.
"""
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

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


@contextmanager
def api_as(database, monkeypatch):
    """The onboarding and organization routers over real PostgREST, for whichever actor is set."""
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "static")
    with owned_http_service("postgrest", database_url=database.url) as url, httpx.Client(trust_env=False) as transport:
        pg = SyncPostgrestClient(url, http_client=transport)
        client = SimpleNamespace(table=pg.from_, rpc=pg.rpc)
        repo = OrganizationRepository(client)
        app = FastAPI()
        app.include_router(onboarding_router)
        app.include_router(organization_router)
        app.add_exception_handler(AppException, app_exception_handler)
        actor = {"id": database.owner}
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id=actor["id"], email=f"{actor['id']}@example.test", role="authenticated")
        app.dependency_overrides[get_initialization_service] = lambda: UserInitializationService(
            ProfileRepositorySupabase(client), repo)
        app.dependency_overrides[get_org_service] = lambda: OrganizationService(repo)
        with TestClient(app) as api:
            def as_user(user_id):
                actor["id"] = user_id
                return api
            yield as_user


def add_member(database, org_id, role):
    user = str(uuid4())
    database.sql(f"""
        INSERT INTO auth.users(id) VALUES('{user}');
        INSERT INTO profiles(user_id,email) VALUES('{user}','{user}@example.test') ON CONFLICT DO NOTHING;
        INSERT INTO org_members(org_id,user_id,role) VALUES('{org_id}','{user}','{role}');
    """)
    return user


def create_project(database, org_id, user_id):
    """Creation as Cloud admits it: one idempotent operation, decided in SQL."""
    key = str(uuid4())
    digest = hashlib.sha256(key.encode()).hexdigest()
    result = database.sql(
        "SELECT public.create_project_idempotent("
        f"'{key}','{digest}','p-{uuid4().hex[:12]}','Plans',NULL,'{org_id}','{user_id}',"
        f"'{uuid4().hex}','empty')"
    )
    return json.loads(result)


def admitted(result):
    """Created, whether or not its first content is still to come."""
    return result["outcome"] in ("created", "initializing_created")


def footprint(database, org_id):
    return database.sql(
        f"SELECT (SELECT count(*) FROM projects WHERE org_id='{org_id}')"
        f" || '/' || (SELECT count(*) FROM project_create_operations)"
    )


def offered(api, org_id):
    """Whether the account's spaces list offers this one as a destination."""
    rows = {row["id"]: row for row in api.get("/organizations/").json()["data"]}
    return bool(rows.get(org_id, {}).get("can_create_projects"))


def test_a_team_is_offered_as_a_destination_exactly_when_creation_admits_it(database, monkeypatch):
    members = {role: add_member(database, database.team, role) for role in ("member", "viewer")}
    members["owner"] = database.owner
    outsider = add_member(database, add_team(database), "owner")
    outcomes = {}
    with api_as(database, monkeypatch) as api:
        for role, user in {**members, "outsider": outsider}.items():
            before = footprint(database, database.team)
            result = create_project(database, database.team, user)
            outcomes[role] = "admitted" if admitted(result) else result["outcome"]
            assert offered(api(user), database.team) == admitted(result), role
            if not admitted(result):
                # Refused before anything is written: no empty Project, no pending operation.
                assert footprint(database, database.team) == before, role
    # Owners and members may start a Project in a Space; read-only viewers and outsiders cannot.
    assert outcomes == {"member": "admitted", "viewer": "forbidden", "owner": "admitted", "outsider": "forbidden"}


def test_a_personal_space_takes_projects_from_its_owner_only(database, monkeypatch):
    with api_as(database, monkeypatch) as api:
        default_space = api(database.owner).post("/auth/personal-space").json()["data"]
        assert default_space["kind"] == "space" and offered(api(database.owner), default_space["id"])
        assert offered(api(database.owner), database.personal)
        created = create_project(database, database.personal, database.owner)
        assert admitted(created), created
        project = created["project"]["id"]
        assert database.sql(f"SELECT org_id FROM projects WHERE id='{project}'") == database.personal

        # Someone outside this Space is never offered it, and creating in it is refused without a trace.
        assert not offered(api(database.colleague), database.personal)
        before = footprint(database, database.personal)
        assert create_project(database, database.personal, database.colleague)["outcome"] == "forbidden"
        assert footprint(database, database.personal) == before


def add_team(database):
    team = str(uuid4())
    database.sql(f"INSERT INTO organizations(id,name,slug,type,created_by) VALUES('{team}','Other','other-{team}','team','{database.owner}')")
    return team
