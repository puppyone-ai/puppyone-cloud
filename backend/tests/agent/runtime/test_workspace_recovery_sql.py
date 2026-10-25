"""Real PostgreSQL fences for compute recovery, disk loss and deleted-owner reclamation."""

import pytest

from tests.agent.runtime.test_live_workbench_control import claim, default, rpc, transition
from tests.agent.runtime.test_workspace_connections import reserve, update

pytestmark = pytest.mark.integration


def resource(row, args, **changes):
    value = {
        "provider": "docker",
        "resource_id": "machine-one",
        "volume_id": "disk-one",
        "workbench_id": row["id"],
        "workbench_generation": row["generation"],
        "allocation_pending": False,
        "project_id": args["project"],
    }
    value.update(changes)
    return value


def paused(db, args):
    row = claim(db, args, default(db, args))["workbench"]
    row = transition(db, row, "running", resource(row, args))
    row = transition(db, row, "pausing")
    return transition(db, row, "paused")


def resuming(db, args):
    return claim(db, args, paused(db, args))["workbench"]


def expire_operation(db, row):
    db.sql(
        f"UPDATE cloud_workbenches SET operation_until=now()-interval '1 second' WHERE id='{row['id']}'"
    )
    return db.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'")


def test_lost_compute_is_recreated_only_on_the_same_disk(postgres, submitted):
    args, _ = submitted
    row = resuming(postgres, args)
    assert row["state"] == "resuming"
    intent = resource(row, args, allocation_pending=True)
    row = transition(postgres, row, "resuming", intent)
    with pytest.raises(RuntimeError, match="workbench_resource_mismatch"):
        transition(
            postgres,
            row,
            "resuming",
            resource(row, args, resource_id="machine-two", volume_id="disk-two"),
        )
    row = transition(postgres, row, "resuming", resource(row, args, resource_id="machine-two"))
    assert row["resource"]["resource_id"] == "machine-two"
    assert row["resource"]["volume_id"] == "disk-one"
    assert transition(postgres, row, "running")["state"] == "running"


def test_a_lost_disk_retires_the_generation_and_the_next_open_starts_fresh(postgres, submitted):
    args, _ = submitted
    row = resuming(postgres, args)
    postgres.sql(f"UPDATE cloud_workbenches SET unpublished_changes=true WHERE id='{row['id']}'")
    stale = {**row, "version": row["version"] - 1}
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        rpc(
            postgres,
            "disk_lost",
            workbench=row["id"],
            generation=row["generation"],
            version=stale["version"],
            operation=row["operation_id"],
        )
    lost = rpc(
        postgres,
        "disk_lost",
        workbench=row["id"],
        generation=row["generation"],
        version=row["version"],
        operation=row["operation_id"],
    )
    assert lost["state"] == "retired" and lost["resource"] is None
    assert lost["operation_error"] == "workbench_disk_lost" and lost["operation_retryable"]
    assert lost["unpublished_changes"] is False  # No copy remains to protect.
    fresh = claim(postgres, args, lost)["workbench"]
    assert fresh["state"] == "allocating" and fresh["generation"] != row["generation"]


def test_disk_loss_is_only_recorded_while_resuming(postgres, submitted):
    args, _ = submitted
    row = paused(postgres, args)
    with pytest.raises(RuntimeError, match="workbench_transition_invalid"):
        rpc(
            postgres,
            "disk_lost",
            workbench=row["id"],
            generation=row["generation"],
            version=row["version"],
            operation=row["operation_id"],
        )


def test_a_live_owner_keeps_the_unique_copy_protection(postgres, submitted):
    args, _ = submitted
    row = paused(postgres, args)
    postgres.sql(f"UPDATE cloud_workbenches SET unpublished_changes=true WHERE id='{row['id']}'")
    with pytest.raises(RuntimeError, match="workbench_unique_copy_protected"):
        transition(postgres, row, "cleanup_pending")
    assert (
        rpc(
            postgres,
            "reclaim_claim",
            workbench=row["id"],
            generation=row["generation"],
            version=row["version"],
        )["acquired"]
        is False
    )


def test_deleting_a_project_with_an_open_ssh_session_succeeds_and_reclaim_waits(
    postgres, submitted
):
    args, _ = submitted
    row = claim(postgres, args, default(postgres, args))["workbench"]
    row = transition(postgres, row, "running", resource(row, args))
    connection = reserve(postgres, args, row)  # Also marks unpublished changes.
    postgres.sql(f"UPDATE projects SET lifecycle_status='deleting' WHERE id='{args['project']}'")
    row = expire_operation(postgres, row)
    assert row["state"] == "retained" and row["owner_deleted_at"] and row["unpublished_changes"]

    def reclaim(current):
        return rpc(
            postgres,
            "reclaim_claim",
            workbench=current["id"],
            generation=current["generation"],
            version=current["version"],
        )

    assert reclaim(row)["acquired"] is False  # An open SSH session still occupies it.
    update(postgres, connection, "stop")
    update(postgres, connection, "release")
    row = postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'")
    claimed = reclaim(row)
    assert claimed["acquired"] is True
    owned = claimed["workbench"]
    assert owned["state"] == "cleanup_pending"
    assert reclaim(owned)["acquired"] is False  # One owner at a time.
    retired = transition(postgres, owned, "retired")
    assert retired["state"] == "retired" and retired["resource"] is None
