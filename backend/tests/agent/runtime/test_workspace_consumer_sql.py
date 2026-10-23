"""Real database fences for API-owned readiness, including concurrent replies."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.agent.runtime.test_workbench_ownership import call, create, transition
from tests.agent.runtime.test_workspace_readiness_operations import acquire, progress, request

pytestmark = pytest.mark.integration


def test_preflight_probes_exact_postgrest_contract_without_writes(control_api):
    from src.platform.workspace.repository import WorkbenchRepository

    WorkbenchRepository(control_api).verify_opening_contract()


def pending(db, args):
    row = acquire(db, request(db, args, create(db, args)))
    resource = dict(
        provider="third",
        resource_id="owned",
        workbench_id=row["id"],
        workbench_generation=row["generation"],
        allocation_pending=False,
    )
    row = transition(db, row, "allocating", resource)
    return progress(db, row, "checking_files")


def finish(db, args, row, **overrides):
    values = dict(
        workbench=row["id"],
        generation=row["generation"],
        version=row["version"],
        operation=row["operation_id"],
        epoch=row["compute_epoch"],
        resource=row["resource"],
        revision=args["policy"]["revision"],
    )
    return call(db, "finish_open", **{**values, **overrides})


def test_concurrent_consumers_complete_once_and_response_loss_is_idempotent(postgres, submitted):
    args, _ = submitted
    row = pending(postgres, args)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: finish(postgres, args, row), range(8)))
    assert all(r["state"] == "running" and not r["open_requested"] for r in results)
    assert len({r["version"] for r in results}) == 1
    assert finish(postgres, args, row) == results[0]


@pytest.mark.parametrize(
    "change", [{"epoch": 99}, {"operation": str(uuid4())}, {"resource": {}}, {"version": -1}]
)
def test_stale_consumer_cannot_finish_or_replace_resource(postgres, submitted, change):
    args, _ = submitted
    row = pending(postgres, args)
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        finish(postgres, args, row, **change)
    assert postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'") == row


def test_revocation_after_native_read_blocks_ready_transaction(postgres, submitted):
    args, _ = submitted
    user = str(uuid4())
    postgres.sql(f"""
      INSERT INTO auth.users(id) VALUES ('{user}');
      INSERT INTO org_members(org_id,user_id,role)
        SELECT org_id,'{user}','member' FROM projects WHERE id='{args["project"]}';
      INSERT INTO project_members(project_id,org_id,user_id,role)
        SELECT id,org_id,'{user}','editor' FROM projects WHERE id='{args["project"]}';
    """)
    args = {
        **args,
        "user": user,
        "policy": {
            "revision": call(postgres, "context", user=user, project=args["project"])["revision"]
        },
    }
    row = pending(postgres, args)
    postgres.sql(
        f"DELETE FROM project_members WHERE project_id='{args['project']}' AND user_id='{args['user']}';"
    )
    with pytest.raises(RuntimeError, match="agent_context_changed"):
        finish(postgres, args, row)


@pytest.mark.parametrize("field", ["open_deadline", "operation_until"])
def test_expired_handoff_is_fenced(postgres, submitted, field):
    args, _ = submitted
    row = pending(postgres, args)
    postgres.sql(
        f"UPDATE cloud_workbenches SET {field}=now()-interval '1 second' WHERE id='{row['id']}';"
    )
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        finish(postgres, args, row)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_public_roles_cannot_complete_opening(postgres, role):
    with pytest.raises(RuntimeError, match="permission denied"):
        postgres.sql(
            f"SET ROLE {role}; SELECT cloud_workbench_finish_open(null,null,1,null,1,null,null);"
        )


def test_populated_upgrade_keeps_resources_and_finishes_existing_intent():
    import importlib.util

    from tests.agent.runtime.conftest import ROOT, Database

    spec = importlib.util.spec_from_file_location(
        "consumer_pg", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = ROOT / "supabase/migrations/20261023030000_complete_workspace_file_readiness.sql"
    previous = sorted(p.name for p in candidate.parent.glob("*.sql") if p.name < candidate.name)[-1]
    with module.native_postgres(through=previous) as cluster:
        db = Database(cluster["url"])
        user, org, project = [str(uuid4()) for _ in range(3)]
        db.sql(f"""
          BEGIN;
          INSERT INTO auth.users(id) VALUES ('{user}');
          INSERT INTO organizations(id,name,slug,created_by) VALUES ('{org}','Upgrade','upgrade-{org}','{user}');
          INSERT INTO org_members(org_id,user_id,role) VALUES ('{org}','{user}','owner');
          INSERT INTO projects(id,name,org_id,created_by,lifecycle_status) VALUES ('{project}','Upgrade','{org}','{user}','ready');
          INSERT INTO project_members(project_id,org_id,user_id,role) VALUES ('{project}','{org}','{user}','admin');
          COMMIT;
        """)
        args = {
            "user": user,
            "project": project,
            "policy": {"revision": call(db, "context", user=user, project=project)["revision"]},
        }
        waiting = pending(db, args)
        running = pending(db, args)
        running = call(
            db,
            "ready",
            workbench=running["id"],
            generation=running["generation"],
            version=running["version"],
            operation=running["operation_id"],
            resource=running["resource"],
        )
        db.sql(candidate.read_text())
        for before in [waiting, running]:
            assert db.row(f"SELECT * FROM cloud_workbenches WHERE id='{before['id']}'") == before
        assert finish(db, args, waiting)["state"] == "running"
