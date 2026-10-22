"""Upgrade populated pre-readiness workspaces without changing their ownership/files."""

import importlib.util
from uuid import uuid4

import pytest

from tests.agent.runtime.conftest import ROOT, Database
from tests.agent.runtime.test_workbench_ownership import call, transition

pytestmark = pytest.mark.integration


def test_populated_upgrade_preserves_running_and_pausing_resources():
    module_spec = importlib.util.spec_from_file_location(
        "readiness_pg", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    candidate = ROOT / "supabase/migrations/20261023010000_workspace_readiness_operations.sql"
    previous = sorted(p.name for p in candidate.parent.glob("*.sql") if p.name < candidate.name)[-1]
    with module.native_postgres(through=previous) as cluster:
        db = Database(cluster["url"])
        user, org, project = map(str, (uuid4(), uuid4(), uuid4()))
        db.sql(f"""
          BEGIN;
          INSERT INTO auth.users(id) VALUES ('{user}');
          INSERT INTO organizations(id,name,slug,created_by) VALUES ('{org}','Upgrade','upgrade-{org}','{user}');
          INSERT INTO org_members(org_id,user_id,role) VALUES ('{org}','{user}','owner');
          INSERT INTO projects(id,name,org_id,created_by,lifecycle_status) VALUES ('{project}','Upgrade','{org}','{user}','ready');
          INSERT INTO project_members(project_id,org_id,user_id,role) VALUES ('{project}','{org}','{user}','admin');
          COMMIT;
        """)
        revision = call(db, "context", user=user, project=project)["revision"]
        rows = []
        for state in ("running", "pausing", "paused"):
            row = call(
                db,
                "create",
                user=user,
                project=project,
                name=state,
                target_ref="refs/heads/" + state,
                revision=revision,
            )
            row = call(
                db,
                "acquire",
                workbench=row["id"],
                user=user,
                project=project,
                binding={"provider": "fly", "artifact": "existing-image"},
            )["workbench"]
            resource = {
                "provider": "fly",
                "resource_id": str(uuid4()),
                "volume_id": str(uuid4()),
                "workbench_id": row["id"],
                "workbench_generation": row["generation"],
                "allocation_pending": False,
            }
            row = transition(db, row, "running", resource)
            if state != "running":
                row = transition(db, row, "pausing")
            if state == "paused":
                row = transition(db, row, "paused")
            rows.append(row)
        db.sql(candidate.read_text())
        for before in rows:
            after = db.row(f"SELECT * FROM cloud_workbenches WHERE id='{before['id']}'")
            assert {key: after[key] for key in before} == before
            assert not after["open_requested"] and after["operation_error"] is None
        pending = call(
            db,
            "request_open",
            workbench=rows[1]["id"],
            user=user,
            project=project,
            binding=rows[1]["binding"],
            revision=revision,
        )
        assert pending["open_requested"] and pending["state"] == "pausing"
