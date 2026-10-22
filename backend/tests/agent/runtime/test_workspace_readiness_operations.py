"""Real SQL fencing and durable open intent, including a third provider."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.agent.runtime.test_workbench_ownership import call, create

pytestmark = pytest.mark.integration


def request(db, args, row, provider="third"):
    return call(
        db,
        "request_open",
        workbench=row["id"],
        user=args["user"],
        project=args["project"],
        binding={"provider": provider, "artifact": "pinned"},
        revision=args["policy"]["revision"],
    )


def acquire(db, row):
    return call(
        db,
        "acquire",
        workbench=row["id"],
        user=row["user_id"],
        project=row["project_id"],
        binding=row["binding"],
    )["workbench"]


def progress(db, row, phase, **kw):
    return call(
        db,
        "progress",
        workbench=row["id"],
        generation=row["generation"],
        operation=row["operation_id"],
        phase=phase,
        **kw,
    )


def test_clicks_share_one_deadline_and_worker_claim(postgres, submitted):
    args, _ = submitted
    row = create(postgres, args)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: request(postgres, args, row), range(8)))
    assert len({r["open_deadline"] for r in replies}) == 1
    assert all(r["state"] == "retired" and r["resource"] is None for r in replies)
    claimed = acquire(postgres, replies[0])
    assert claimed["state"] == "allocating" and claimed["binding"]["provider"] == "third"
    assert not call(
        postgres,
        "acquire",
        workbench=row["id"],
        user=args["user"],
        project=args["project"],
        binding=claimed["binding"],
    )["acquired"]
    # Live claims are not repeatedly dispatched by every worker.
    assert (
        postgres.sql(
            f"SELECT count(*) FROM cloud_workbench_pending('third') WHERE id='{row['id']}'"
        )
        == "0"
    )


def test_readiness_publishes_atomically_and_stale_owners_cannot_overwrite(postgres, submitted):
    args, _ = submitted
    row = acquire(postgres, request(postgres, args, create(postgres, args)))
    progress(postgres, row, "checking_files")
    resource = dict(
        provider="third",
        resource_id="owned",
        workbench_id=row["id"],
        workbench_generation=row["generation"],
        allocation_pending=False,
    )
    ready = call(
        postgres,
        "ready",
        workbench=row["id"],
        generation=row["generation"],
        version=row["version"],
        operation=row["operation_id"],
        resource=resource,
    )
    assert ready["state"] == "running" and not ready["open_requested"]
    assert ready["operation_phase"] == "ready" and ready["operation_error"] is None
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        progress(
            postgres,
            {**row, "operation_id": str(uuid4())},
            "failed",
            error="workbench_start_timeout",
        )


def test_failure_preserves_resource_and_retry_keeps_generation(postgres, submitted):
    args, _ = submitted
    row = acquire(postgres, request(postgres, args, create(postgres, args)))
    failed = progress(postgres, row, "failed", error="workbench_provider_authentication")
    assert failed["state"] == "allocating" and not failed["open_requested"]
    assert not failed["operation_retryable"]
    retried = acquire(postgres, request(postgres, args, failed))
    assert retried["generation"] == row["generation"]
    assert retried["compute_epoch"] == row["compute_epoch"]
    assert retried["operation_id"] != row["operation_id"]
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        progress(postgres, row, "connecting")


def test_expiry_cannot_cancel_a_newer_open(postgres, submitted):
    args, _ = submitted
    row = request(postgres, args, create(postgres, args))
    postgres.sql(
        f"UPDATE cloud_workbenches SET open_deadline=now()-interval '1 second' WHERE id='{row['id']}'"
    )
    expired = postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'")
    new = request(postgres, args, expired)
    result = call(postgres, "expire_open", workbench=row["id"], deadline=expired["open_deadline"])
    assert result["open_requested"] and result["open_deadline"] == new["open_deadline"]


def test_open_while_pausing_survives_pause_ack(postgres, submitted):
    from tests.agent.runtime.test_live_workbench_control import running

    args, _ = submitted
    row = running(postgres, args)
    pausing = call(
        postgres,
        "suspend",
        workbench=row["id"],
        generation=row["generation"],
        version=row["version"],
    )["workbench"]
    # Match the existing binding; a wake request never routes to another provider.
    requested = call(
        postgres,
        "request_open",
        workbench=row["id"],
        user=args["user"],
        project=args["project"],
        binding=pausing["binding"],
        revision=args["policy"]["revision"],
    )
    assert requested["state"] == "pausing" and requested["open_requested"]
    paused = call(
        postgres,
        "transition",
        workbench=row["id"],
        generation=row["generation"],
        version=pausing["version"],
        operation=pausing["operation_id"],
        state="paused",
    )
    result = progress(postgres, paused, "paused")
    assert result["open_requested"]
    resumed = acquire(postgres, result)
    assert resumed["state"] == "resuming" and resumed["generation"] == row["generation"]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_open_control_is_not_public(postgres, role):
    with pytest.raises(RuntimeError, match="permission denied"):
        postgres.sql(f"SET ROLE {role}; SELECT * FROM cloud_workbench_pending('third');")


def test_expiry_fences_the_timed_out_worker_before_user_retry(postgres, submitted):
    args, _ = submitted
    row = acquire(postgres, request(postgres, args, create(postgres, args)))
    postgres.sql(
        f"UPDATE cloud_workbenches SET open_deadline=now()-interval '1 second' WHERE id='{row['id']}'"
    )
    overdue = postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{row['id']}'")
    expired = call(postgres, "expire_open", workbench=row["id"], deadline=overdue["open_deadline"])
    assert expired["operation_error"] == "workbench_start_timeout"
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        progress(postgres, row, "checking_files")
    fresh = acquire(postgres, request(postgres, args, expired))
    assert fresh["operation_id"] != row["operation_id"]
    assert fresh["generation"] == row["generation"]
