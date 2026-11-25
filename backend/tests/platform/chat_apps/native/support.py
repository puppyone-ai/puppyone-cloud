"""Real PostgreSQL 17 with the whole migration chain, and a few project fixtures."""

import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[5]
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


class CommandError(RuntimeError):
    pass


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
            raise CommandError(result.stderr)
        return result.stdout.strip()

    def json(self, query):
        value = self.sql(query)
        return json.loads(value) if value else None

    def human(self, action, user, **payload):
        return self.json(
            f"SELECT public.chat_app_command({literal(action)},{literal(user)},{literal(payload)});"
        )

    def gateway(self, action, worker="gateway-a", **payload):
        return self.json(
            "SELECT public.chat_app_gateway_command("
            f"{literal(action)},{literal(worker)},{literal(payload)});"
        )


def project(db, role="admin"):
    """A ready project with one manager; returns (project_id, manager_id, org_id)."""
    user, org, project_id = str(uuid4()), str(uuid4()), str(uuid4())
    db.sql(f"""
      BEGIN;
      INSERT INTO auth.users(id) VALUES ('{user}');
      INSERT INTO public.profiles(user_id,email,display_name) VALUES ('{user}','{user}@example.test','Alex')
        ON CONFLICT (user_id) DO UPDATE SET display_name=EXCLUDED.display_name;
      INSERT INTO public.organizations(id,name,slug,created_by) VALUES ('{org}','Chat','chat-{org}','{user}');
      INSERT INTO public.org_members(org_id,user_id,role) VALUES ('{org}','{user}','owner');
      INSERT INTO public.projects(id,name,org_id,created_by,lifecycle_status)
        VALUES ('{project_id}','My project','{org}','{user}','ready');
      INSERT INTO public.project_members(project_id,org_id,user_id,role)
        VALUES ('{project_id}','{org}','{user}','{role}');
      COMMIT;
    """)
    return project_id, user, org


def member(db, project_id, org, role):
    user = str(uuid4())
    db.sql(f"""
      BEGIN;
      INSERT INTO auth.users(id) VALUES ('{user}');
      INSERT INTO public.profiles(user_id,email,display_name) VALUES ('{user}','{user}@example.test','Mia')
        ON CONFLICT (user_id) DO UPDATE SET display_name=EXCLUDED.display_name;
      INSERT INTO public.org_members(org_id,user_id,role) VALUES ('{org}','{user}','member');
      INSERT INTO public.project_members(project_id,org_id,user_id,role)
        VALUES ('{project_id}','{org}','{user}','{role}');
      COMMIT;
    """)
    return user


def code(n):
    return f"{n:064x}"
