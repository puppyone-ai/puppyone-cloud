"""Real PostgreSQL fencing for external connections, alongside existing Agent tasks."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.agent.runtime.test_live_workbench_control import rpc, running

pytestmark = pytest.mark.integration


def reserve(db, args, row, identity=None, **changes):
    values = dict(
        id=identity or str(uuid4()),
        workbench=row["id"],
        generation=row["generation"],
        user=args["user"],
        project=args["project"],
        revision=rpc(db, "context", user=args["user"], project=args["project"])["revision"],
    )
    return rpc(db, "connection_acquire", **{**values, **changes})


def update(db, connection, action):
    return rpc(
        db,
        "connection_update",
        id=connection["id"],
        generation=connection["generation"],
        action=action,
    )


def test_external_connection_retains_occupancy_until_stop_ack(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    connection = reserve(postgres, args, row)
    assert connection["resource"]["resource_id"] == row["resource"]["resource_id"]
    assert connection["task_uid"] == row["next_task_uid"]
    postgres.sql(
        f"UPDATE cloud_workbench_connections SET lease_until=now()-interval '1 minute' WHERE id='{connection['id']}'"
    )
    assert (
        postgres.sql(f"SELECT cloud_workbench_has_occupants('{row['id']}','{row['generation']}')")
        == "t"
    )
    with pytest.raises(RuntimeError, match="cleanup_required"):
        update(postgres, connection, "release")
    with pytest.raises(RuntimeError, match="expired"):
        update(postgres, connection, "renew")
    with pytest.raises(RuntimeError, match="cleanup_required"):
        postgres.sql(f"DELETE FROM cloud_workbench_connections WHERE id='{connection['id']}'")
    with pytest.raises(RuntimeError, match=r"active_occupants|cleanup_required"):
        postgres.sql(f"UPDATE cloud_workbenches SET state='pausing' WHERE id='{row['id']}'")
    update(postgres, connection, "stop")
    assert update(postgres, connection, "release")["state"] == "released"
    assert update(postgres, connection, "release")["state"] == "released"


def test_connection_identity_is_single_use_and_never_recycled(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    with ThreadPoolExecutor(max_workers=2) as pool:
        connections = list(pool.map(lambda _: reserve(postgres, args, row), range(2)))
    assert len({c["task_uid"] for c in connections}) == 2
    for c in connections:
        update(postgres, c, "stop")
        update(postgres, c, "release")
    with pytest.raises(RuntimeError, match="already_used"):
        reserve(postgres, args, row, connections[0]["id"])
    c = reserve(postgres, args, row)
    assert c["task_uid"] > max(x["task_uid"] for x in connections)
    update(postgres, c, "stop")
    update(postgres, c, "release")


def test_connection_owner_generation_and_revision_fail_closed(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    for changes in ({"user": str(uuid4())}, {"generation": str(uuid4())}, {"revision": {}}):
        with pytest.raises(RuntimeError):
            reserve(postgres, args, row, **changes)
    assert (
        postgres.sql(
            f"SELECT count(*) FROM cloud_workbench_connections WHERE workbench_id='{row['id']}'"
        )
        == "0"
    )
    assert (
        postgres.sql(
            "SELECT has_table_privilege('authenticated','cloud_workbench_connections','SELECT')"
        )
        == "f"
    )
    assert (
        postgres.sql(
            "SELECT has_table_privilege('service_role','cloud_workbench_connections','UPDATE')"
        )
        == "f"
    )
    assert (
        postgres.sql("SELECT has_table_privilege('authenticated','workspace_ssh_grants','SELECT')")
        == "f"
    )


def test_populated_upgrade_preserves_workbench_and_existing_occupants(postgres, submitted):
    """Apply just this additive migration over the exact preceding local schema."""
    import importlib.util

    from tests.agent.runtime.conftest import ROOT, Database

    spec = importlib.util.spec_from_file_location(
        "ssh_upgrade_pg", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = "ssh_upgrade_" + uuid4().hex
    postgres.sql(f"CREATE DATABASE {database}")
    scratch = Database(postgres.url.rsplit("/", 1)[0] + "/" + database)
    try:
        scratch.sql((ROOT / "scripts/testing/postgres_auth_stub.sql").read_text())
        candidate = ROOT / "supabase/migrations/20261023020000_hosted_workspace_connections.sql"
        for path in sorted((ROOT / "supabase/migrations").glob("*.sql")):
            if path.name >= candidate.name:
                break
            scratch.sql(module.product_migration_sql(path))
        args, _ = submitted
        user, project = args["user"], args["project"]
        org = postgres.row(f"SELECT org_id FROM projects WHERE id='{project}'")["org_id"]
        scratch.sql(f"""
          BEGIN;
          INSERT INTO auth.users(id) VALUES('{user}');
          INSERT INTO organizations(id,name,slug,created_by) VALUES('{org}','Preserved','ssh-{org}','{user}');
          INSERT INTO org_members(org_id,user_id,role) VALUES('{org}','{user}','owner');
          INSERT INTO projects(id,name,org_id,created_by,lifecycle_status) VALUES('{project}','Preserved','{org}','{user}','ready');
          INSERT INTO project_members(project_id,org_id,user_id,role) VALUES('{project}','{org}','{user}','admin');
          COMMIT;
        """)
        row = running(scratch, args)
        task_id, run_id = str(uuid4()), str(uuid4())
        scratch.sql(f"""INSERT INTO cloud_workbench_tasks(execution_id,workbench_id,generation,run_id,fence,task_uid,resource)
            VALUES('{task_id}','{row["id"]}','{row["generation"]}','{run_id}',1,20000,'{{}}');
            UPDATE cloud_workbenches SET next_task_uid=20001 WHERE id='{row["id"]}';""")
        old_task = scratch.row(
            f"SELECT * FROM cloud_workbench_tasks WHERE execution_id='{task_id}'"
        )
        before = scratch.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'")
        scratch.sql(candidate.read_text().replace("COMMIT;", "ROLLBACK;"))
        assert (
            scratch.sql("SELECT to_regclass('public.cloud_workbench_connections') IS NULL") == "t"
        )
        assert scratch.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'") == before
        scratch.sql(candidate.read_text())
        assert scratch.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'") == before
        assert (
            scratch.row(f"SELECT * FROM cloud_workbench_tasks WHERE execution_id='{task_id}'")
            == old_task
        )
        connection = reserve(scratch, args, row)
        assert connection["task_uid"] == 20001
        assert connection["resource"]["resource_id"] == row["resource"]["resource_id"]
        update(scratch, connection, "stop")
        update(scratch, connection, "release")
        # The deployed contract follows the entire candidate chain. The
        # preservation assertions above isolate this migration's own upgrade.
        for path in sorted(candidate.parent.glob("*.sql")):
            if path.name > candidate.name:
                scratch.sql(module.product_migration_sql(path))
        scratch.sql((ROOT / "supabase/tests/_support/cloud_agent_contracts.inc").read_text())
    finally:
        postgres.sql(f"DROP DATABASE {database} WITH (FORCE)")


def test_reaper_stale_scan_does_not_stop_a_renewed_connection(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    connection = reserve(postgres, args, row)
    assert update(postgres, connection, "renew")["state"] == "active"
    assert update(postgres, connection, "expire")["state"] == "active"
    update(postgres, connection, "stop")
    update(postgres, connection, "release")
