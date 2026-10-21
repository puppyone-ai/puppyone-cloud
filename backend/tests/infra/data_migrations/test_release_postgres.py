"""Real release journal, serialization and lock-loss tests on an owned PG cluster."""

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from src.infra.data_migrations.database import PsqlClient
from src.infra.data_migrations.errors import ExecutionError, MigrationBusyError
from src.infra.data_migrations.release.journal import Journal
from src.infra.data_migrations.release.plan import Phase, ReleasePlan
from src.infra.data_migrations.release.schema import Schema
from src.infra.data_migrations.release.schema_only import deploy_schema
from src.infra.data_migrations.release.target import DatabaseTarget

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.integration


def run(*args, **kwargs):
    return subprocess.run(
        list(map(str, args)), check=True, capture_output=True, text=True, timeout=60, **kwargs
    ).stdout.strip()


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    if os.environ.get("PUPPYONE_RELEASE_PG_TEST") != "1":
        pytest.skip("explicit owned-cluster test; set PUPPYONE_RELEASE_PG_TEST=1")
    executable = shutil.which("initdb")
    assert executable, "PostgreSQL 17 binaries must be on PATH"
    binaries = Path(executable).parent
    root = tmp_path_factory.mktemp("release-pg")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    run(binaries / "initdb", "-D", root / "db", "-U", "postgres", "--auth=trust", "--no-locale")
    try:
        run(
            binaries / "pg_ctl",
            "-D",
            root / "db",
            "-l",
            root / "server.log",
            "-w",
            "-o",
            f"-p {port} -h 127.0.0.1 -k /tmp",
            "start",
        )
        url = f"postgresql://postgres@127.0.0.1:{port}/postgres?sslmode=disable&application_name=release-test"
        db = PsqlClient(url)
        db.scalar("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;")
        yield db
    finally:
        if (root / "db/postmaster.pid").exists():
            run(binaries / "pg_ctl", "-D", root / "db", "-m", "fast", "-w", "stop")


@pytest.fixture
def journal(cluster, tmp_path):
    cluster.scalar("DROP SCHEMA IF EXISTS puppyone_release CASCADE")
    control = tmp_path / "supabase/releases/control.sql"
    control.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "supabase/releases/control.sql", control)
    run("git", "init", "-q", tmp_path)

    def commit(text):
        (tmp_path / "release.txt").write_text(text)
        run("git", "add", ".", cwd=tmp_path)
        run(
            "git",
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release@example.invalid",
            "commit",
            "-qm",
            text,
            cwd=tmp_path,
        )
        return run("git", "rev-parse", "HEAD", cwd=tmp_path)

    old, new = commit("old"), commit("new")
    return Journal(tmp_path, cluster), old, new


def test_journal_records_verified_phases_and_resumes_after_process_loss(journal):
    value, old, _ = journal
    assert value.begin(old, "checksum")
    value.checkpoint("backup", {"restore_point_ref": "owned.dump", "restore_verified": True})
    resumed = Journal(value.root, value.db)
    assert resumed.begin(old, "checksum")
    assert resumed.evidence("backup")["restore_point_ref"] == "owned.dump"
    resumed.finish("accepted", "accepted")
    assert not Journal(value.root, value.db).begin(old, "checksum")


def test_old_accepted_source_cannot_roll_back_newer_failed_release(journal):
    value, old, new = journal
    value.begin(old, "old-plan")
    value.finish("accepted", "accepted")
    value.begin(new, "new-plan")
    value.finish("failed", "data")
    with pytest.raises(ValueError, match="descend"):
        value.begin(old, "old-plan")
    assert value.begin(new, "new-plan")


def test_forward_fix_supersedes_failure_but_preserves_audit(journal):
    value, old, new = journal
    value.begin(old, "old-plan")
    value.checkpoint("failure", {"phase": "data"})
    value.finish("failed", "data")
    assert value.begin(new, "new-plan")
    assert (
        value.db.scalar(
            "SELECT state FROM puppyone_release.runs WHERE source_sha=:'sha'",
            variables={"sha": old},
        )
        == "superseded"
    )
    with pytest.raises(ValueError, match="superseded"):
        value.begin(old, "old-plan")


def test_changed_plan_for_same_source_is_rejected(journal):
    value, old, _ = journal
    value.begin(old, "one")
    with pytest.raises(ValueError, match="plan changed"):
        value.begin(old, "two")


def test_database_not_ci_alone_serializes_release(cluster):
    with cluster.advisory_lock("puppyone-environment-release") as first:
        first.assert_held()
        with (
            pytest.raises(MigrationBusyError),
            cluster.advisory_lock("puppyone-environment-release"),
        ):
            pytest.fail("second release acquired the same database")
    with cluster.advisory_lock("puppyone-environment-release") as next_release:
        next_release.assert_held()


def test_lost_lock_is_detected_before_next_release_operation(cluster):
    with (
        pytest.raises(ExecutionError, match="lock connection was lost"),
        cluster.advisory_lock("puppyone-environment-release") as guard,
    ):
        cluster.scalar(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='release-test' AND pid<>pg_backend_pid()"
        )
        guard.assert_held()


def test_release_receipts_are_not_accessible_to_product_roles(journal):
    value, old, _ = journal
    value.begin(old, "plan")
    for role in ("anon", "authenticated", "service_role"):
        assert (
            value.db.scalar(f"SELECT has_schema_privilege('{role}','puppyone_release','USAGE')")
            == "f"
        )


def test_release_contract_probes_can_write_but_leave_no_rows(cluster, tmp_path, monkeypatch):
    support = tmp_path / "supabase/tests/_support"
    support.mkdir(parents=True)
    (tmp_path / "supabase/migrations").mkdir()
    (support / "schema_contracts.inc").write_text(
        "BEGIN; CREATE TABLE public.release_probe(id integer); "
        "INSERT INTO public.release_probe VALUES(1); ROLLBACK;"
    )
    for name in ("data_api_containment.inc", "cloud_agent_contracts.inc"):
        (support / name).write_text("BEGIN READ ONLY; SELECT 1; ROLLBACK;")
    monkeypatch.setattr(cluster, "applied_schema_versions", lambda: set())
    schema = Schema(tmp_path, cluster)
    monkeypatch.setattr(schema, "verify_drift", lambda: None)
    schema.verify()
    assert cluster.scalar("SELECT to_regclass('public.release_probe') IS NULL") == "t"


def test_ordinary_schema_lane_applies_missing_older_sql_with_official_cli(
    cluster, tmp_path, monkeypatch
):
    assert shutil.which("supabase"), "The pinned Supabase CLI is required"
    migrations = tmp_path / "supabase/migrations"
    migrations.mkdir(parents=True)
    shutil.copyfile(ROOT / "supabase/config.toml", tmp_path / "supabase/config.toml")
    support = tmp_path / "supabase/tests/_support"
    support.mkdir(parents=True)
    for name in ("schema_contracts.inc", "data_api_containment.inc", "cloud_agent_contracts.inc"):
        (support / name).write_text("BEGIN READ ONLY; SELECT id FROM public.schema_lane_probe; ROLLBACK;")
    releases = tmp_path / "supabase/releases"
    releases.mkdir()
    (releases / "legacy_credential_preflight.sql").write_text("BEGIN READ ONLY; SELECT 1; ROLLBACK;")
    versions = ["20260101000000", "20260102000000", "20260103000000"]
    for version in versions:
        (migrations / f"{version}_expand_probe.sql").write_text(
            "CREATE TABLE public.schema_lane_probe(id integer);" if version == versions[1] else "SELECT 1;"
        )
    cluster.scalar(
        "CREATE SCHEMA IF NOT EXISTS supabase_migrations; "
        "CREATE TABLE supabase_migrations.schema_migrations(version text PRIMARY KEY, statements text[], name text); "
        "INSERT INTO supabase_migrations.schema_migrations(version) VALUES('20260101000000'),('20260103000000');"
    )
    target = object.__new__(DatabaseTarget)
    target.root, target.db = tmp_path, cluster
    target.schema_executor = Schema(tmp_path, cluster)
    # History is already normalized; Docker-backed drift is covered by the
    # release rehearsal. CLI application, history, locks and probes are real.
    monkeypatch.setattr(target.schema_executor, "adopt", lambda: None)
    monkeypatch.setattr(target.schema_executor, "verify_drift", lambda: None)
    plan = ReleasePlan((Phase("sql", "schema", "latest"),), "sum", frozenset(versions))
    try:
        assert deploy_schema(plan, target) == {"state": "deployed", "pending_versions": [versions[1]]}
        assert cluster.applied_schema_versions() == set(versions)
        assert deploy_schema(plan, target) == {"state": "deployed", "pending_versions": []}
        assert cluster.scalar("SELECT count(*) FROM public.schema_lane_probe") == "0"
    finally:
        cluster.scalar("DROP TABLE IF EXISTS public.schema_lane_probe; DROP SCHEMA supabase_migrations CASCADE;")
