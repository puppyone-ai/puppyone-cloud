from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.infra.data_migrations.database import PsqlClient
from src.infra.data_migrations.errors import PrerequisiteError

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "release_connection", ROOT / "supabase/releases/connection.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REF = "a" * 20


def test_static_connection_keeps_password_out_of_cli_arguments():
    result = module.connection_environment(
        {
            "SUPABASE_PROJECT_ID": REF,
            "DATABASE_URL": f"postgresql://postgres.{REF}:hello%40world@aws-0.pooler.supabase.com:5432/postgres?sslmode=require",
        }
    )
    assert result["PGPASSWORD"] == "hello@world"
    assert result["PGDATABASE"] == "postgres"
    assert "hello" not in result["SUPABASE_DATABASE_URL"]
    assert result["PGUSER"] == "postgres." + REF


@pytest.mark.parametrize(
    "uri",
    [
        "postgresql://postgres.other:secret@aws-0.pooler.supabase.com:5432/postgres",
        f"postgresql://postgres:secret@db.{REF}.supabase.co:6543/postgres",
        "postgresql://postgres:secret@attacker.invalid:5432/postgres",
    ],
)
def test_connection_rejects_wrong_project_or_transaction_pooler(uri):
    with pytest.raises(ValueError, match="protected project"):
        module.connection_environment({"SUPABASE_PROJECT_ID": REF, "DATABASE_URL": uri})


def test_expiring_cli_login_must_match_same_protected_project(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    client = PsqlClient(
        f"postgresql://cli_login_abc.{REF}:secret@aws-0.pooler.supabase.com:5432/postgres"
    )
    client.assert_supabase_target(project_ref=REF, api_url=f"https://{REF}.supabase.co")
    with pytest.raises(PrerequisiteError):
        client.assert_supabase_target(
            project_ref="b" * 20, api_url="https://" + "b" * 20 + ".supabase.co"
        )
