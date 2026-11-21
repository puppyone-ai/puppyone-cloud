"""Real PostgreSQL 17 fixtures for workspace backup and shared-project-VM tests."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[5]


def _psql_path() -> str:
    postgres_bin = (
        shutil.which("postgres")
        or "/usr/lib/postgresql/17/bin/postgres"
        if Path("/usr/lib/postgresql/17/bin/postgres").exists()
        else "/opt/homebrew/opt/postgresql@17/bin/postgres"
    )
    return str(Path(postgres_bin).with_name("psql"))


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


class NativeDatabase:
    def __init__(self, url: str):
        self.url = url
        self.psql = _psql_path()

    def sql(self, value: str) -> str:
        result = subprocess.run(
            [self.psql, self.url, "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
            input=value,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def row(self, query: str):
        value = self.sql("SELECT to_jsonb(value) FROM (" + query + ") value;")
        return json.loads(value) if value else None

    def wb_rpc(self, function: str, **values):
        params = ",".join("p_" + key + "=>" + literal(value) for key, value in values.items())
        return self.row(f"SELECT public.cloud_workbench_{function}({params}) AS result")["result"]

    def call_rpc(self, full_name: str, **values):
        params = ",".join("p_" + key + "=>" + literal(value) for key, value in values.items())
        return self.row(f"SELECT to_jsonb(public.{full_name}({params})) AS result")["result"]


@pytest.fixture(scope="session")
def pg():
    spec = importlib.util.spec_from_file_location(
        "workspace_native_postgres", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.native_postgres() as cluster:
        yield NativeDatabase(cluster["url"])


def seed_project(pg: NativeDatabase, *, extra_members: list[tuple[str, str]] | None = None) -> dict:
    """Create a ready project with one owner/admin and optional extra members."""
    owner = str(uuid4())
    org = str(uuid4())
    project = str(uuid4())
    statements = [
        "BEGIN;",
        f"INSERT INTO auth.users(id) VALUES ('{owner}');",
        f"INSERT INTO public.organizations(id,name,slug,created_by) VALUES ('{org}','Workspace fixture','ws-{org}','{owner}');",
        f"INSERT INTO public.org_members(org_id,user_id,role) VALUES ('{org}','{owner}','owner');",
        f"INSERT INTO public.projects(id,name,org_id,created_by,lifecycle_status) VALUES ('{project}','Workspace fixture','{org}','{owner}','ready');",
        f"INSERT INTO public.project_members(project_id,org_id,user_id,role) VALUES ('{project}','{org}','{owner}','admin');",
    ]
    members: dict[str, str] = {"owner": owner}
    for label, role in extra_members or []:
        uid = str(uuid4())
        members[label] = uid
        statements.append(f"INSERT INTO auth.users(id) VALUES ('{uid}');")
        statements.append(
            f"INSERT INTO public.org_members(org_id,user_id,role) VALUES ('{org}','{uid}','member');"
        )
        statements.append(
            f"INSERT INTO public.project_members(project_id,org_id,user_id,role) VALUES ('{project}','{org}','{uid}','{role}');"
        )
    statements.append("COMMIT;")
    pg.sql("\n".join(statements))
    return {"org": org, "project": project, "owner": owner, **members}


def make_running_workbench(pg: NativeDatabase, user: str, project: str) -> dict:
    ctx = pg.wb_rpc("context", user=user, project=project)
    row = pg.wb_rpc("default", user=user, project=project, revision=ctx["revision"])
    claimed = pg.wb_rpc(
        "acquire",
        workbench=row["id"],
        user=user,
        project=project,
        binding={"provider": "docker", "artifact": "pinned-artifact"},
    )["workbench"]
    return pg.wb_rpc(
        "transition",
        workbench=claimed["id"],
        generation=claimed["generation"],
        version=claimed["version"],
        operation=claimed["operation_id"],
        state="running",
        resource={
            "provider": "docker",
            "resource_id": "vm-1",
            "workbench_id": claimed["id"],
            "workbench_generation": claimed["generation"],
            "allocation_pending": False,
            "project_id": project,
        },
    )
