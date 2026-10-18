"""A1 production-entry checks for default private workspace admission.

Invoke this non-default filename explicitly. This is NOT full-chain acceptance:
No provider, dispatcher or billing lifecycle is exercised here. Routing, JWT
verification, DI, admission and SQL are real; identities use a test signing key.
"""

import secrets
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest

from src.config import settings
from src.main import create_app
from src.platform.access.adapters.agent import dependencies as agent_dependencies
from src.platform.auth import dependencies as auth_dependencies
from src.platform.project.write_lease import ProjectWriteLease
from tests.agent.runtime.conftest import (
    control_api as control_api,
)
from tests.agent.runtime.conftest import (
    object_endpoint as object_endpoint,
)
from tests.agent.runtime.conftest import (
    postgres as postgres,
)
from tests.agent.runtime.conftest import (
    services as services,
)
from tests.repository_hosting.integration.test_repository_file_policy import enroll_file_policy
from tests.repository_hosting.integration.test_repository_logical_billing import enroll_billing

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def entry(services, postgres, monkeypatch):
    client, _storage, engine = services
    user, project, org, agent = [str(uuid4()) for _ in range(4)]
    # Fixture identity/repository enrollment only. Never insert a workbench, Run,
    # task, resource, ready state or receipt to get past a missing product step.
    postgres.sql(f"""
      BEGIN;
      INSERT INTO auth.users(id) VALUES ('{user}');
      INSERT INTO organizations(id,name,slug,created_by) VALUES ('{org}','Acceptance','accept-{org}','{user}');
      INSERT INTO org_members(org_id,user_id,role) VALUES ('{org}','{user}','owner');
      INSERT INTO projects(id,name,org_id,created_by,lifecycle_status) VALUES ('{project}','Acceptance','{org}','{user}','ready');
      INSERT INTO project_members(project_id,org_id,user_id,role) VALUES ('{project}','{org}','{user}','admin');
      INSERT INTO access_surfaces(id,project_id,org_id,kind,name,created_by,config)
        VALUES ('{agent}','{project}','{org}','agent','Agent','{user}',
          '{{"llm_model":"fixture","bash_view":{{"path_prefix":"","excludes":[],"max_mode":"rw"}}}}');
      INSERT INTO access_surfaces(project_id,org_id,kind,name,created_by)
        VALUES ('{project}','{org}','git_remote','Git','{user}');
      INSERT INTO version_repositories(project_id,authority,object_format) VALUES ('{project}','native','sha1');
      INSERT INTO version_repository_refs(project_id,name,object_format,symbolic_target)
        VALUES ('{project}',decode('48454144','hex'),'sha1',decode('726566732f68656164732f6d61696e','hex'));
      INSERT INTO version_organization_capacity(org_id,initialized) VALUES ('{org}',true);
      INSERT INTO version_repository_capacity(project_id,org_id,initialized) VALUES ('{project}','{org}',true);
      COMMIT;
    """)
    enrolled = enroll_billing(SimpleNamespace(pg=postgres, project=project, org=org))
    enroll_file_policy(enrolled, maximum=65536)
    ops = engine.product_operations().for_user(project, user)
    async with ProjectWriteLease(project, "acceptance.seed"):
        await ops.write_file(project, "seed.txt", b"seed", who="user:" + user)
    monkeypatch.setattr(agent_dependencies, "_agent_service", None)
    monkeypatch.setattr(auth_dependencies, "_auth_service", None)
    monkeypatch.setattr(settings, "SKIP_AUTH", False)
    monkeypatch.setattr(settings, "JWT_SECRET", secrets.token_hex(32))
    monkeypatch.setattr(settings, "SUPABASE_PUBLIC_URL", "http://owned.invalid")
    app = create_app()
    app.state.version_engine = engine
    token = jwt.encode(
        {
            "sub": user,
            "aud": "authenticated",
            "iss": "http://owned.invalid/auth/v1",
            "role": "authenticated",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    assert auth_dependencies.get_current_user not in app.dependency_overrides
    assert agent_dependencies.get_agent_service not in app.dependency_overrides
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://owned.invalid",
            headers={"Authorization": "Bearer " + token},
        ) as http:
            yield SimpleNamespace(
                http=http,
                postgres=postgres,
                project=project,
                agent=agent,
                client=client,
                user=user,
                org=org,
                app=app,
                engine=engine,
            )
    finally:
        # Close only owned fixture rows; provider fixtures clean their own compute.
        postgres.sql(f"UPDATE agent_runs SET state='failed' WHERE project_id='{project}'")


def request(case, **values):
    return {
        "project_id": case.project,
        "agent_id": case.agent,
        "request_id": str(uuid4()),
        "prompt": "Read seed.txt",
        **values,
    }


def assert_no_run_or_session_resource(case):
    for table in ("agent_runs", "agent_session_workspaces"):
        assert (
            case.postgres.sql(f"SELECT count(*) FROM {table} WHERE project_id='{case.project}'")
            == "0"
        )


async def test_production_entry_shares_default_identity_without_request_owned_compute(entry):
    runs = []
    for _ in range(2):
        response = await entry.http.post("/api/v1/agents/runs", json=request(entry))
        assert response.status_code == 202, response.text
        runs.append(response.json())
    assert runs[0]["workbench_id"] == runs[1]["workbench_id"]
    assert runs[0]["session_id"] != runs[1]["session_id"]
    assert (
        entry.postgres.sql(
            f"SELECT count(*) FROM agent_session_workspaces WHERE project_id='{entry.project}'"
        )
        == "0"
    )
    row = entry.postgres.row(
        f"SELECT * FROM cloud_workbenches WHERE id='{runs[0]['workbench_id']}'"
    )
    assert row["is_default"] and row["resource"] is None and row["state"] == "retired"


async def test_production_entry_has_private_workspace_admission_not_an_unconfigured_port(entry):
    response = await entry.http.post(
        "/api/v1/agents/runs", json=request(entry, workbench_id=str(uuid4()))
    )
    assert response.status_code == 404, (
        "A1: an unknown private workspace must reach real ownership admission, not an absent composition; "
        f"got {response.status_code}: {response.text}"
    )
    assert_no_run_or_session_resource(entry)
