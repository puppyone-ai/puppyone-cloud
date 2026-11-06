"""Real PostgreSQL populated upgrade; no hosted database or external storage."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[5]
MIGRATION = "20261107120000_expand_personal_cloud_spaces.sql"
PSQL = str(Path(shutil.which("postgres") or "/opt/homebrew/opt/postgresql@17/bin/postgres").with_name("psql"))


@pytest.fixture(scope="module")
def database():
    spec = importlib.util.spec_from_file_location("personal_native_postgres", ROOT / "scripts/testing/native_postgres.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.native_postgres(through="20261107110000_expand_host_connection_records.sql") as cluster:
        def sql(statement):
            result = subprocess.run([PSQL, cluster["url"], "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
                                    input=statement, text=True, capture_output=True, timeout=60)
            if result.returncode:
                raise RuntimeError(result.stderr)
            return result.stdout.strip()

        owner, colleague, team, project = (str(uuid4()) for _ in range(4))
        sql(f"""
            BEGIN;
            INSERT INTO auth.users(id) VALUES('{owner}'),('{colleague}');
            INSERT INTO profiles(user_id,email) VALUES('{owner}','owner@example.test'),('{colleague}','colleague@example.test') ON CONFLICT DO NOTHING;
            INSERT INTO organizations(id,name,slug,type,created_by)
              VALUES('{team}','Legacy personal label','personal-{owner}','personal','{owner}');
            INSERT INTO org_members(org_id,user_id,role)
              VALUES('{team}','{owner}','owner'),('{team}','{colleague}','member');
            INSERT INTO projects(id,name,org_id,created_by,lifecycle_status) VALUES('{project}','Existing','{team}','{owner}','ready');
            INSERT INTO project_members(project_id,org_id,user_id,role) VALUES('{project}','{team}','{owner}','admin');
            UPDATE profiles SET default_org_id='{team}' WHERE user_id='{owner}';
            COMMIT;
        """)
        before = json.loads(sql(f"SELECT jsonb_build_object('org',(SELECT to_jsonb(o) FROM organizations o WHERE id='{team}'),"
                                f"'project',(SELECT to_jsonb(p) FROM projects p WHERE id='{project}'))"))
        sql((ROOT / "supabase/migrations" / MIGRATION).read_text())
        yield SimpleNamespace(sql=sql, url=cluster["url"], owner=owner, colleague=colleague, team=team, project=project, before=before)
