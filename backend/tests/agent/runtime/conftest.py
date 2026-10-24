"""Owned real PostgreSQL with the complete application migration chain."""

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[4]
PSQL = str(
    Path(shutil.which("postgres") or "/opt/homebrew/opt/postgresql@17/bin/postgres").with_name(
        "psql"
    )
)


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


def claimant(name, *, pool="default"):
    """The worker identity a dispatcher of ``pool`` claims with, named for the test."""
    from src.platform.access.adapters.agent.runtime.dispatch import WorkerIdentity

    return WorkerIdentity(
        id=name,
        pool=pool,
        service="agent_worker",
        environment="test",
        host="test",
        build="test",
        provider="docker",
    ).claim()


class Database:
    def __init__(self, url):
        self.url = url

    def sql(self, value):
        result = subprocess.run(
            [PSQL, self.url, "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
            input=value,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def rpc(self, function, **values):
        args = ",".join("p_" + key + "=>" + literal(value) for key, value in values.items())
        output = self.sql(f"SELECT to_jsonb(public.agent_run_{function}({args}));")
        return json.loads(output) if output else None

    def row(self, query):
        value = self.sql("SELECT to_jsonb(value) FROM (" + query + ") value;")
        return json.loads(value) if value else None


@pytest.fixture(scope="session")
def postgres():
    spec = importlib.util.spec_from_file_location(
        "agent_native_postgres", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.native_postgres() as cluster:
        yield Database(cluster["url"])


@pytest.fixture
def submitted(postgres):
    uid, project, org, agent, request = [str(uuid4()) for _ in range(5)]
    postgres.sql(f"""
      BEGIN;
      INSERT INTO auth.users(id) VALUES ('{uid}');
      INSERT INTO public.organizations(id,name,slug,created_by) VALUES ('{org}','Agent fixture','agent-{org}','{uid}');
      INSERT INTO public.org_members(org_id,user_id,role) VALUES ('{org}','{uid}','owner');
      INSERT INTO public.projects(id,name,org_id,created_by,lifecycle_status) VALUES ('{project}','Agent fixture','{org}','{uid}','ready');
      INSERT INTO public.project_members(project_id,org_id,user_id,role) VALUES ('{project}','{org}','{uid}','admin');
      INSERT INTO public.access_surfaces(id,project_id,org_id,kind,name,created_by) VALUES ('{agent}','{project}','{org}','agent','Agent fixture','{uid}');
      COMMIT;
    """)
    policy = {
        "model": "fixture",
        "revision": postgres.row(
            f"SELECT public.authorization_revision_token('{project}') AS token"
        )["token"],
        "surface_updated_at": postgres.row(
            f"SELECT updated_at FROM access_surfaces WHERE id='{agent}'"
        )["updated_at"],
    }
    arguments = dict(
        user=uid,
        project=project,
        agent=agent,
        session=None,
        request=request,
        digest="a" * 64,
        prompt="test durable task",
        policy=policy,
        timeout=600,
    )
    yield arguments, postgres.rpc("submit", **arguments)
    # Failed runs with retained resources remain claimable for cleanup after
    # lease expiry. Retire both selectors in this owned fixture so later tests
    # cannot take over a previous case's intentionally uncertain recovery.
    postgres.sql(f"""
        UPDATE agent_runs SET state='failed',snapshot=snapshot-'resource_retained'
        WHERE project_id='{project}';
    """)


@pytest.fixture(scope="session")
def control_api(postgres):
    """Actual PostgREST against the owned PostgreSQL, never a remote service."""
    import httpx
    from postgrest import SyncPostgrestClient

    from src.infra.supabase.instrumentation import DatabaseTransport
    from tests.agent.runtime.owned_http import owned_http_service

    with owned_http_service("postgrest", database_url=postgres.url) as url:
        transport = DatabaseTransport(path_prefix="/")
        with httpx.Client(trust_env=False, transport=transport) as http:
            client = SyncPostgrestClient(url, http_client=http)

            class Adapter:
                def table(self, name):
                    return client.from_(name)

                def rpc(self, *args, **kwargs):
                    return client.rpc(*args, **kwargs)

            adapter = Adapter()
            adapter.transport = transport
            yield adapter


@pytest.fixture(scope="session")
def object_endpoint():
    from tests.agent.runtime.owned_http import owned_http_service

    with owned_http_service("minio") as url:
        yield url


@pytest.fixture
def services(control_api, object_endpoint, monkeypatch):
    from src.config import settings
    from src.infra.s3.config import s3_settings
    from src.infra.s3.service import S3Service
    from src.infra.supabase import dependencies
    from src.infra.supabase.client import SupabaseClient
    from src.version_engine.bootstrap.container import build_version_engine_container

    monkeypatch.setenv(
        "NO_PROXY", ",".join(filter(None, [os.environ.get("NO_PROXY"), "127.0.0.1,localhost"]))
    )
    for key, value in {
        "S3_ENDPOINT_URL": object_endpoint,
        "S3_ACCESS_KEY_ID": "agent-fixture",
        "S3_SECRET_ACCESS_KEY": "agent-fixture-password",
        "S3_REGION": "us-east-1",
        "S3_BUCKET_NAME": "agent-" + str(uuid4()),
    }.items():
        monkeypatch.setattr(s3_settings, key, value)
    monkeypatch.setattr(SupabaseClient(), "_client", control_api)
    monkeypatch.setattr(dependencies, "_supabase_client", control_api)
    monkeypatch.setattr(dependencies, "_supabase_repository", None)
    monkeypatch.setattr(settings, "MANAGED_AI_ENABLED", True)
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "disabled")
    storage = S3Service()
    storage.client.create_bucket(Bucket=storage.bucket_name)
    container = build_version_engine_container(s3=storage, supabase=SupabaseClient(), probe=True)
    return control_api, storage, container


@pytest.fixture(autouse=True)
async def owned_worker_resources(monkeypatch):
    """Only discard recovery volumes allocated by this isolated test's workers."""
    from src.platform.workspace.execution.pi_worker import PiWorker

    allocated = []
    create = PiWorker.create

    async def tracked(worker):
        result = await create(worker)
        allocated.append((dict(worker.resource), worker.store))
        return result

    monkeypatch.setattr(PiWorker, "create", tracked)
    yield
    volumes = set()
    for resource, store in allocated:
        if resource["provider"] == "docker":
            await PiWorker.cleanup(resource, store=store)
            volumes.add(resource["recovery_volume"])
    for volume in volumes:
        result = subprocess.run(["docker", "volume", "rm", volume], capture_output=True, text=True)
        assert result.returncode == 0 or "no such volume" in result.stderr, result.stderr
