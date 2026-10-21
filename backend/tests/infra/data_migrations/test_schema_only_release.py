"""Schema releases apply all pending SQL without application-service credentials."""

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.infra.data_migrations.release.plan import Phase, ReleasePlan
from src.infra.data_migrations.release.schema import Schema
from src.infra.data_migrations.release.schema_only import deploy_schema
from src.infra.data_migrations.release.target import DatabaseTarget


@pytest.fixture
def target(tmp_path):
    migrations = tmp_path / "supabase/migrations"
    migrations.mkdir(parents=True)
    for version in ("20261001000000", "20261002000000", "20261003000000"):
        (migrations / f"{version}_expand_fixture.sql").write_text("SELECT 1;")
    applied = {"20261001000000", "20261003000000"}
    events = []
    db = SimpleNamespace(
        scalar=lambda sql: "f",
        applied_schema_versions=lambda: set(applied),
        receipt=lambda _: {"verified": True, "artifact_checksum": "sum"},
        command=lambda *args, **kwargs: events.append("credential-preflight"),
    )
    value = object.__new__(DatabaseTarget)
    value.root, value.db = tmp_path, db
    value.catalog = SimpleNamespace(get=lambda _: SimpleNamespace(checksum="sum"))
    value.schema_executor = Schema(tmp_path, db)
    value.schema_executor.adopt = lambda: events.append("adopt")
    value.events, value.applied, value.held = events, applied, False

    def assert_held():
        if not value.held:
            raise RuntimeError("lost lock")

    @contextmanager
    def lock():
        value.held = True
        try:
            yield SimpleNamespace(assert_held=assert_held)
        finally:
            value.held = False

    def apply(through):
        assert_held()
        for file in value.schema_executor.pending(through):
            applied.add(file.name[:14])
            events.append(file.name[:14])

    def verify():
        assert_held()
        assert applied == set(value.schema_executor.files)
        events.append("verified")

    value.lock = lock
    value.schema_executor.apply = apply
    value.schema_executor.verify = verify
    return value


PLAN = ReleasePlan(
    (Phase("old-copy", "data", "copy"), Phase("current", "schema", "latest")),
    "sum", frozenset(),
)


def test_missing_older_sql_is_applied_without_any_platform_client_and_retry_is_noop(target):
    # Newer timestamps do not hide an unapplied older migration. The target has
    # no platform: an accidental Redis/Railway/build/deploy call would fail.
    result = deploy_schema(PLAN, target)
    assert result == {"state": "deployed", "pending_versions": ["20261002000000"]}
    assert target.events == ["adopt", "credential-preflight", "20261002000000", "verified"]
    assert deploy_schema(PLAN, target) == {"state": "deployed", "pending_versions": []}
    assert target.events.count("20261002000000") == 1
    assert not target.held


def test_pending_data_hands_off_before_any_candidate_sql(target):
    target.db.receipt = lambda _: None
    result = deploy_schema(PLAN, target)
    assert result["state"] == "coordinated_release_required"
    assert result["pending_data"] == ["copy"]
    assert target.events == ["adopt"]


@pytest.mark.parametrize("filename,body", [
    ("20261002000000_contract_fixture.sql", "SELECT 1;"),
    ("20261002000000_retire_fixture.sql", "-- requires-data-migration: copy\nSELECT 1;"),
])
def test_contract_hands_off_even_if_data_receipt_exists(target, filename, body):
    file = target.root / "supabase/migrations" / filename
    file.write_text(body)
    target.schema_executor.files["20261002000000"] = file
    assert deploy_schema(PLAN, target)["state"] == "coordinated_release_required"
    assert target.events == ["adopt"]


def test_tampered_data_receipt_is_not_treated_as_completed(target):
    target.db.receipt = lambda _: {"verified": True, "artifact_checksum": "wrong"}
    with pytest.raises(ValueError, match="differs"):
        deploy_schema(PLAN, target)
    assert target.events == ["adopt"]


def test_future_or_unknown_target_schema_rejects_stale_release(target):
    target.applied.add("20271001000000")
    with pytest.raises(ValueError, match="history"):
        deploy_schema(PLAN, target)
    assert target.events == ["adopt"]


def test_verification_failure_is_not_a_successful_schema_release(target):
    def fail():
        raise RuntimeError("database verification failed")
    target.schema_executor.verify = fail
    with pytest.raises(RuntimeError, match="verification"):
        deploy_schema(PLAN, target)
    assert not target.held


def test_lost_lock_stops_before_sql(target):
    target.db.command = lambda *args, **kwargs: setattr(target, "held", False)
    with pytest.raises(RuntimeError, match="lost lock"):
        deploy_schema(PLAN, target)
    assert "20261002000000" not in target.events


ROOT = Path(__file__).resolve().parents[4]


def test_schema_job_requires_only_database_credentials_and_never_a_release_config():
    workflow = yaml.safe_load((ROOT / ".github/workflows/_environment-release.yml").read_text())
    database = workflow["jobs"]["database"]
    assert database["needs"] == "admission"
    assert database["environment"] == "${{ inputs.environment }}"
    steps = database["steps"]
    sql_index = next(i for i, s in enumerate(steps) if s.get("id") == "schema")
    assert "PUPPYONE_RELEASE_CONFIG" not in str(steps[:sql_index + 1])
    assert "schema_only" in steps[sql_index]["run"]
    assert all("continue-on-error" not in s for s in steps)
    release = workflow["jobs"]["release"]
    assert release["needs"] == "database"
    assert "coordinated_release_required" in release["if"]
    assert "application_configured" in release["if"]


@pytest.mark.parametrize("admission,database,state,configured,release,failed,ready", [
    ("success", "success", "deployed", "false", "skipped", False, True),
    ("success", "success", "deployed", "true", "success", False, True),
    ("success", "success", "deployed", "true", "failure", True, True),
    ("success", "success", "coordinated_release_required", "false", "failure", True, False),
    ("success", "success", "coordinated_release_required", "true", "success", False, True),
    ("success", "success", "coordinated_release_required", "false", "skipped", True, False),
    ("failure", "skipped", "", "", "skipped", True, False),
    ("success", "failure", "", "", "skipped", True, False),
    ("success", "cancelled", "", "", "skipped", True, False),
    ("success", "success", "", "false", "skipped", True, False),
])
def test_workflow_never_confuses_skipped_migration_with_success(
    admission, database, state, configured, release, failed, ready
):
    workflow = yaml.safe_load((ROOT / ".github/workflows/_environment-release.yml").read_text())
    step = workflow["jobs"]["result"]["steps"][0]
    harness = """
      const output = {failed: [], rows: []};
      const context = {sha: 'fixture-sha'};
      const core = {setFailed: m => output.failed.push(m), summary: {
        addHeading() {return this}, addTable(rows) {output.rows = rows; return this},
        async write() {}
      }};
    """ + "(async () => {" + step["with"]["script"] + "\n})().then(() => console.log(JSON.stringify(output)));"
    result = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, check=True,
        env={**os.environ, "ADMISSION_RESULT": admission, "DATABASE_RESULT": database,
             "SCHEMA_STATE": state, "APPLICATION_CONFIGURED": configured, "RELEASE_RESULT": release},
    )
    output = json.loads(result.stdout)
    assert bool(output["failed"]) is failed
    assert (output["rows"][0][1] == "Migrated and verified") is ready
