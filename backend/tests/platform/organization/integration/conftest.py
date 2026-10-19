"""Real JWT → production routers → PostgreSQL/PostgREST → MinIO acceptance.

The only external substitutes are the identity-provider JWKS fallback (tokens
are genuinely signed/verified locally), email delivery (local outbox), and the
paid billing provider (disabled). No authorization or persistence is mocked.
"""

import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from src.config import settings
from src.exception_handler import app_exception_handler
from src.exceptions import AppException
from src.infra.supabase.repository import SupabaseRepository
from src.platform.auth import dependencies as auth_dependencies
from src.platform.auth.service import AuthService
from src.platform.authorization.repository import ProjectMembershipRepository
from src.platform.organization.dependencies import get_org_service
from src.platform.organization.repository import OrganizationRepository
from src.platform.organization.service import InviteEmailResult, OrganizationService
from src.platform.project.dependencies import (
    get_project_membership_repository,
    get_project_repository,
)
from src.platform.project.repository import ProjectRepositorySupabase
from src.platform.project.write_lease import ProjectWriteLease
from src.utils.middleware import RequestContextMiddleware
from src.file_operations.commands import VersionWriteCommandService
from src.version_engine.bootstrap.dependencies import (
    get_product_operation_adapter,
    get_version_write_command_service,
)
from tests.agent.runtime import conftest as owned
from tests.agent.runtime.test_supervisor import prepared as prepared_fixture

postgres = owned.postgres
control_api = owned.control_api
object_endpoint = owned.object_endpoint
services = owned.services
submitted = owned.submitted
prepared = prepared_fixture

JWT_SECRET = "owned-organization-acceptance-secret-not-for-production"


@pytest.fixture
async def team(prepared, services, postgres, monkeypatch):
    c = prepared
    api, _storage, container = services
    org = postgres.row(f"SELECT org_id FROM projects WHERE id='{c.project}'")["org_id"]
    users = {
        name: str(uuid4())
        for name in ("admin", "editor", "viewer", "member", "outsider", "invitee")
    }
    users["owner"] = c.user
    emails = {name: f"{name}-{uid}@example.test" for name, uid in users.items()}
    for name, uid in users.items():
        postgres.sql(f"""
            INSERT INTO auth.users(id,email) VALUES('{uid}','{emails[name]}')
            ON CONFLICT(id) DO UPDATE SET email=EXCLUDED.email;
            INSERT INTO profiles(user_id,email,display_name) VALUES('{uid}','{emails[name]}','{name}')
            ON CONFLICT(user_id) DO UPDATE SET email=EXCLUDED.email,display_name=EXCLUDED.display_name;
        """)
        if name not in ("owner", "outsider", "invitee"):
            postgres.sql(
                f"INSERT INTO org_members(org_id,user_id,role) VALUES('{org}','{uid}','member')"
            )
        if name in ("admin", "editor", "viewer"):
            postgres.sql(
                f"INSERT INTO project_members(project_id,org_id,user_id,role) VALUES('{c.project}','{org}','{uid}','{name}')"
            )
    other_org, other_project = str(uuid4()), str(uuid4())
    postgres.sql(f"""
        BEGIN;
        UPDATE organizations SET seat_limit=100 WHERE id='{org}';
        UPDATE projects SET visibility='private' WHERE id='{c.project}';
        INSERT INTO organizations(id,name,slug,created_by) VALUES('{other_org}','Other team','other-{other_org}','{users["outsider"]}');
        INSERT INTO org_members(org_id,user_id,role) VALUES('{other_org}','{users["outsider"]}','owner');
        INSERT INTO projects(id,name,org_id,created_by,lifecycle_status,visibility)
            VALUES('{other_project}','Other workspace','{other_org}','{users["outsider"]}','ready','org');
        INSERT INTO project_members(project_id,org_id,user_id,role)
            VALUES('{other_project}','{other_org}','{users["outsider"]}','admin');
        COMMIT;
    """)
    monkeypatch.setattr(settings, "SKIP_AUTH", False)
    monkeypatch.setattr(settings, "JWT_SECRET", JWT_SECRET)
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "SUPABASE_PUBLIC_URL", "http://127.0.0.1:54321")
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "disabled")
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "disabled")
    # Invalid local tokens may try JWKS; this offline provider cannot authorize.
    monkeypatch.setattr(
        auth_dependencies,
        "_auth_service",
        AuthService(SimpleNamespace(auth=SimpleNamespace(get_claims=lambda _: None))),
    )
    org_service = OrganizationService(OrganizationRepository(api))
    outbox = []

    def deliver(invitation, invite_url, org_name):
        outbox.append({"email": invitation.email, "url": invite_url, "org": org_name})
        return InviteEmailResult(sent=True)

    monkeypatch.setattr(org_service, "send_invite_email", deliver)
    from src.platform.access.adapters.agent.dependencies import get_agent_service
    from src.platform.access.adapters.agent.router import router as agent_router
    from src.platform.organization.router import router as org_router
    from src.platform.project.router import router as project_router
    from src.platform.access.adapters.api.read import read_router
    from src.platform.access.adapters.api.resource import resource_router
    from src.platform.access.adapters.api.write import write_router

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_middleware(RequestContextMiddleware)
    for router in (org_router, project_router, agent_router):
        app.include_router(router, prefix="/api/v1")
    for router in (resource_router, read_router, write_router):
        app.include_router(router, prefix="/api/v1/content")
    project_repo = ProjectRepositorySupabase(SupabaseRepository())
    app.dependency_overrides[get_org_service] = lambda: org_service
    app.dependency_overrides[get_project_repository] = lambda: project_repo
    app.dependency_overrides[get_project_membership_repository] = lambda: (
        ProjectMembershipRepository(api)
    )
    app.dependency_overrides[get_product_operation_adapter] = container.product_operations
    app.dependency_overrides[get_version_write_command_service] = lambda: (
        VersionWriteCommandService(container.product_operations())
    )
    app.dependency_overrides[get_agent_service] = lambda: c.service

    def token(name, **overrides):
        now = int(time.time())
        return jwt.encode(
            {
                "sub": users[name],
                "email": emails[name],
                "role": "authenticated",
                "aud": "authenticated",
                "iss": settings.SUPABASE_PUBLIC_URL + "/auth/v1",
                "iat": now,
                "exp": now + 3600,
                **overrides,
            },
            JWT_SECRET,
            algorithm="HS256",
        )

    async with ProjectWriteLease(c.project, "fixture.organization-acceptance"):
        await c.ops.bulk_write(
            c.project, {"文档/hello #1.md": b"team confidential content"}, who="user:" + c.user
        )
    with TestClient(app) as http:

        def request(name, method, path, **kwargs):
            headers = {"Authorization": f"Bearer {token(name)}"} if name else {}
            return http.request(method, "/api/v1" + path, headers=headers, **kwargs)

        yield SimpleNamespace(
            **vars(c),
            org=org,
            users=users,
            emails=emails,
            request=request,
            token=token,
            http=http,
            app=app,
            outbox=outbox,
            org_service=org_service,
            other_org=other_org,
            other_project=other_project,
        )
