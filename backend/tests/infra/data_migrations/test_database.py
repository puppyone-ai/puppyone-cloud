from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.infra.data_migrations.database import PsqlClient
from src.infra.data_migrations.errors import (
    ExecutionError,
    MigrationBusyError,
    PrerequisiteError,
)


def test_database_url_is_split_into_libpq_environment(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, str], str | None]] = []

    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"], kwargs["input"]))
        return subprocess.CompletedProcess(command, 0, stdout="1\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = PsqlClient(
        "postgresql://operator:sec%40ret@example.test:5433/postgres"
        "?sslmode=require&application_name=puppyone-db"
    )

    assert client.scalar("SELECT 1") == "1"
    command, environment, input_text = calls[0]
    assert all("secret" not in argument for argument in command)
    assert environment["PGHOST"] == "example.test"
    assert environment["PGPORT"] == "5433"
    assert environment["PGDATABASE"] == "postgres"
    assert environment["PGUSER"] == "operator"
    assert environment["PGPASSWORD"] == "sec@ret"
    assert environment["PGSSLMODE"] == "require"
    assert environment["PGAPPNAME"] == "puppyone-db"
    assert "-c" not in command
    assert input_text == "SELECT 1\n"


def test_scalar_variables_use_psql_literal_quoting_over_stdin(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = PsqlClient("postgresql://example.test/postgres")

    client.scalar(
        "SELECT :'migration_id'",
        variables={"migration_id": "20260712_example"},
    )

    assert "-c" not in calls[0]["command"]
    assert "migration_id=20260712_example" in calls[0]["command"]
    assert calls[0]["input"] == "SELECT :'migration_id'\n"


@pytest.mark.parametrize(
    "database_url",
    (
        "not-a-url",
        "https://example.test/postgres",
        "postgresql:///postgres",
        "postgresql://example.test",
        "postgresql://example.test:invalid/postgres",
    ),
)
def test_invalid_database_url_is_rejected(monkeypatch, database_url: str) -> None:
    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")

    with pytest.raises(PrerequisiteError, match="database|PostgreSQL"):
        PsqlClient(database_url)


def test_psql_timeout_is_an_operator_error(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = PsqlClient("postgresql://example.test/postgres")

    with pytest.raises(ExecutionError, match="timed out"):
        client.command(["-c", "SELECT 1"], timeout=1)


@pytest.mark.parametrize(
    "database_url",
    (
        "postgresql://postgres:secret@db.abcdefghijkl.supabase.co:5432/postgres",
        "postgresql://postgres.abcdefghijkl:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
    ),
)
def test_hosted_api_and_database_targets_must_match(monkeypatch, database_url: str) -> None:
    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")
    client = PsqlClient(database_url)

    client.assert_supabase_target(
        project_ref="abcdefghijkl",
        api_url="https://abcdefghijkl.supabase.co",
    )

    with pytest.raises(PrerequisiteError, match="cannot be proven"):
        client.assert_supabase_target(
            project_ref="differentproject",
            api_url="https://differentproject.supabase.co",
        )


def test_hosted_api_url_cannot_disagree_with_project_ref(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")
    client = PsqlClient("postgresql://postgres:secret@db.abcdefghijkl.supabase.co:5432/postgres")

    with pytest.raises(PrerequisiteError, match="SUPABASE_URL"):
        client.assert_supabase_target(
            project_ref="abcdefghijkl",
            api_url="https://differentproject.supabase.co",
        )


def test_transaction_pooler_is_rejected_for_session_locking(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")
    client = PsqlClient(
        "postgresql://postgres.abcdefghijkl:secret@"
        "aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    )

    with pytest.raises(PrerequisiteError, match="session-pooler"):
        client.assert_supabase_target(
            project_ref="abcdefghijkl",
            api_url="https://abcdefghijkl.supabase.co",
        )


def test_sql_receipt_is_insert_only(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    entrypoint = tmp_path / "run.sql"
    verification = tmp_path / "verify.sql"
    entrypoint.write_text("SELECT 1;\n")
    verification.write_text("SELECT 1;\n")
    client = PsqlClient("postgresql://example.test/postgres")

    client.run_sql_transaction(
        migration_id="20260712_example",
        entrypoint_path=entrypoint,
        verify_path=verification,
        checksum="a" * 64,
        source_sha="b" * 40,
        legacy=False,
        timeout=10,
    )

    rendered = str(calls[0]["input"])
    assert "ON CONFLICT (name) DO NOTHING" in rendered
    assert "DO UPDATE" not in rendered
    assert "pg_try_advisory_xact_lock" in rendered
    assert "BEGIN;" in rendered
    assert "SELECT 1;" in rendered
    assert "COMMIT;" in rendered
    assert "-c" not in calls[0]["command"]


def test_standalone_verification_is_read_only_and_database_timed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    verification = tmp_path / "verify.sql"
    verification.write_text("SELECT 1;\n")
    client = PsqlClient("postgresql://example.test/postgres")

    client.verify(verification, timeout=10)

    rendered = str(calls[0]["input"])
    assert "BEGIN READ ONLY;" in rendered
    assert "ROLLBACK;" in rendered
    assert "statement_timeout_ms=10000ms" in calls[0]["command"]
    assert "-c" not in calls[0]["command"]


def test_sql_migration_fails_fast_when_another_runner_holds_the_lock(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda executable: f"/usr/bin/{executable}")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="ERROR: DATA_MIGRATION_BUSY:20260712_example",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    entrypoint = tmp_path / "run.sql"
    verification = tmp_path / "verify.sql"
    entrypoint.write_text("SELECT 1;\n")
    verification.write_text("SELECT 1;\n")
    client = PsqlClient("postgresql://example.test/postgres")

    with pytest.raises(MigrationBusyError, match="already running"):
        client.run_sql_transaction(
            migration_id="20260712_example",
            entrypoint_path=entrypoint,
            verify_path=verification,
            checksum="a" * 64,
            source_sha="b" * 40,
            legacy=False,
            timeout=10,
        )
