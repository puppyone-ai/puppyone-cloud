"""Real PostgreSQL 17 tests for workspace backup claims, save outcomes, and activity renewal."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from .conftest import make_running_workbench, seed_project

ROOT = Path(__file__).resolve().parents[5]


def test_cloud_agent_contracts_passes_with_backup_claim(pg):
    contract_sql = (ROOT / "supabase/tests/_support/cloud_agent_contracts.inc").read_text(
        encoding="utf-8"
    )
    pg.sql(contract_sql)


def test_backup_claim_exclusivity_idle_fence_and_save_outcome(pg):
    env = seed_project(pg)
    wb = make_running_workbench(pg, env["owner"], env["project"])
    ctx = pg.wb_rpc("context", user=env["owner"], project=env["project"])

    # Clean workspace with no unpublished changes -> backup_claim returns acquired=false
    pg.sql(f"UPDATE cloud_workbenches SET unpublished_changes=false WHERE id='{wb['id']}'")
    first = pg.wb_rpc(
        "backup_claim",
        workbench=wb["id"],
        generation=wb["generation"],
        interval_seconds=300,
    )
    assert first["acquired"] is False

    # Record a file write -> sets unpublished_changes=true and updates last_activity_at
    pg.wb_rpc(
        "activity",
        workbench=wb["id"],
        generation=wb["generation"],
        user=env["owner"],
        project=env["project"],
        revision=ctx["revision"],
        write=True,
    )

    # First backup claim acquires and sets backup_until
    claimed = pg.wb_rpc(
        "backup_claim",
        workbench=wb["id"],
        generation=wb["generation"],
        interval_seconds=300,
    )
    assert claimed["acquired"] is True
    assert claimed["workbench"]["backup_until"] is not None

    # Second concurrent worker cannot claim while backup_until is active
    contender = pg.wb_rpc(
        "backup_claim",
        workbench=wb["id"],
        generation=wb["generation"],
        interval_seconds=300,
    )
    assert contender["acquired"] is False

    # Idle pause cannot overlap with an active backup_until lease
    pg.sql(
        f"UPDATE cloud_workbenches SET last_activity_at=now()-interval '2 hours' WHERE id='{wb['id']}'"
    )
    current = pg.row(f"SELECT * FROM cloud_workbenches WHERE id='{wb['id']}'")
    idle = pg.wb_rpc(
        "idle_claim",
        workbench=current["id"],
        generation=current["generation"],
        version=current["version"],
        action="pause",
    )
    assert idle["acquired"] is False

    # Running save_outcome records oid, save_finished_at, clears unpublished_changes and backup_until
    oid = "a" * 40
    saved = pg.wb_rpc(
        "save_outcome",
        workbench=wb["id"],
        generation=wb["generation"],
        status="saved",
        error=None,
        oid=oid,
    )
    assert saved["save_status"] == "saved"
    assert saved["saved_oid"] == oid
    assert saved["unpublished_changes"] is False
    assert saved["backup_until"] is None
    assert saved["save_finished_at"] is not None

    # After interval elapses with NO new activity -> backup_claim still returns acquired=false
    pg.sql(
        f"UPDATE cloud_workbenches SET save_finished_at=now()-interval '400 seconds', "
        f"last_activity_at=now()-interval '500 seconds' WHERE id='{wb['id']}'"
    )
    quiet = pg.wb_rpc(
        "backup_claim",
        workbench=wb["id"],
        generation=wb["generation"],
        interval_seconds=300,
    )
    assert quiet["acquired"] is False

    # Newer activity but interval NOT yet elapsed -> acquired=false
    pg.sql(
        f"UPDATE cloud_workbenches SET save_finished_at=now()-interval '100 seconds', "
        f"last_activity_at=now()-interval '10 seconds' WHERE id='{wb['id']}'"
    )
    too_soon = pg.wb_rpc(
        "backup_claim",
        workbench=wb["id"],
        generation=wb["generation"],
        interval_seconds=300,
    )
    assert too_soon["acquired"] is False

    # Newer activity AND interval elapsed -> acquired=true
    pg.sql(
        f"UPDATE cloud_workbenches SET save_finished_at=now()-interval '400 seconds', "
        f"last_activity_at=now()-interval '100 seconds' WHERE id='{wb['id']}'"
    )
    due = pg.wb_rpc(
        "backup_claim",
        workbench=wb["id"],
        generation=wb["generation"],
        interval_seconds=300,
    )
    assert due["acquired"] is True


def test_ssh_connection_renew_advances_last_activity_at(pg):
    env = seed_project(pg)
    wb = make_running_workbench(pg, env["owner"], env["project"])
    ctx = pg.wb_rpc("context", user=env["owner"], project=env["project"])
    conn_id = str(uuid4())
    pg.wb_rpc(
        "connection_acquire",
        id=conn_id,
        workbench=wb["id"],
        generation=wb["generation"],
        user=env["owner"],
        project=env["project"],
        revision=ctx["revision"],
    )
    pg.sql(
        f"UPDATE cloud_workbenches SET last_activity_at=now()-interval '10 minutes' WHERE id='{wb['id']}'"
    )
    before = pg.row(f"SELECT last_activity_at FROM cloud_workbenches WHERE id='{wb['id']}'")[
        "last_activity_at"
    ]
    pg.wb_rpc("connection_update", id=conn_id, generation=wb["generation"], action="renew")
    after = pg.row(f"SELECT last_activity_at FROM cloud_workbenches WHERE id='{wb['id']}'")[
        "last_activity_at"
    ]
    assert after > before


def test_record_sync_marks_what_main_brought_in_as_unsaved(pg):
    env = seed_project(pg)
    wb = make_running_workbench(pg, env["owner"], env["project"])
    pg.sql(f"UPDATE cloud_workbenches SET unpublished_changes=false WHERE id='{wb['id']}'")
    oid = "c" * 40

    recorded = pg.wb_rpc("record_sync", workbench=wb["id"], generation=wb["generation"], oid=oid)
    assert recorded["synced_oid"] == oid
    # Taken in, not yet on main: the next backup carries it.
    assert recorded["unpublished_changes"] is True

    # Another generation, or a value that is not a commit id, records nothing.
    stale = pg.wb_rpc(
        "record_sync", workbench=wb["id"], generation=str(uuid4()), oid="d" * 40
    )
    assert stale["id"] is None
    with pytest.raises(RuntimeError, match="workbench_sync_oid_invalid"):
        pg.wb_rpc("record_sync", workbench=wb["id"], generation=wb["generation"], oid="main")
    current = pg.row(f"SELECT synced_oid FROM cloud_workbenches WHERE id='{wb['id']}'")
    assert current["synced_oid"] == oid
