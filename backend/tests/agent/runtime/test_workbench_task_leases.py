"""Real database arbitration for task identities and whole-computer transitions."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.agent.runtime.test_workbench_attachment import shared_submit
from tests.agent.runtime.test_workbench_ownership import call, create, transition

pytestmark = pytest.mark.integration


def running(postgres, args):
    created = create(postgres, args)
    row = call(postgres, "acquire", workbench=created["id"], user=args["user"],
               project=args["project"], binding={"provider": "docker", "artifact": "fixture"})["workbench"]
    return transition(postgres, row, "running", {
        "provider": "docker", "resource_id": "fixture-" + row["id"],
        "project_id": args["project"], "session_id": row["generation"],
        "allocation_pending": False, "workbench_id": row["id"],
        "workbench_generation": row["generation"],
    })


def claimed(postgres, args, workbench):
    submitted = shared_submit(postgres, args, workbench)
    run = postgres.rpc("claim_workbench_execution", worker=str(uuid4()))["run"]
    assert run["id"] == submitted["id"]
    return run


def task(postgres, run, workbench):
    return call(postgres, "task_acquire", run=run["id"], execution=run["execution_id"],
                fence=run["fence"], generation=workbench["generation"])


def release(postgres, run, value):
    return call(postgres, "task_released", run=run["id"], execution=run["execution_id"],
                fence=run["fence"], task=value["execution_id"], generation=value["generation"])


def idle_claim(postgres, row, action="pause"):
    return call(postgres, "idle_claim", workbench=row["id"], generation=row["generation"],
                version=row["version"], action=action)


def make_idle(postgres, row):
    postgres.sql(f"UPDATE cloud_workbenches SET last_activity_at=clock_timestamp()-interval '2 days' WHERE id='{row['id']}'")


def test_unique_nonreused_uids_and_durable_resource_attachment(postgres, submitted):
    args, _ = submitted
    wb = running(postgres, args)
    a, b = claimed(postgres, args, wb), claimed(postgres, args, wb)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda r: task(postgres, r, wb), [a, b, a, b]))
    assert rows[0]["task_uid"] != rows[1]["task_uid"]
    assert rows[0] == rows[2] and rows[1] == rows[3]
    for run, row in zip([a, b], rows[:2], strict=True):
        resource = postgres.row(f"SELECT resource FROM agent_run_executions WHERE id='{run['execution_id']}'")["resource"]
        assert resource["task_uid"] == row["task_uid"]
        assert resource["task_execution_id"] == run["execution_id"]
        assert resource["resource_id"] == wb["resource"]["resource_id"]
    release(postgres, a, rows[0])
    c = claimed(postgres, args, wb)
    assert task(postgres, c, wb)["task_uid"] > max(row["task_uid"] for row in rows)


def test_takeover_must_quiesce_only_its_previous_execution(postgres, submitted):
    args, _ = submitted
    wb = running(postgres, args)
    a, b = claimed(postgres, args, wb), claimed(postgres, args, wb)
    old, other = task(postgres, a, wb), task(postgres, b, wb)
    postgres.sql(f"UPDATE agent_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id='{a['id']}'")
    current = postgres.rpc("claim_workbench_execution", worker="takeover")["run"]
    assert current["id"] == a["id"]
    with pytest.raises(RuntimeError, match="workbench_task_cleanup_required"):
        task(postgres, current, wb)
    with pytest.raises(RuntimeError, match="agent_execution_fenced"):
        release(postgres, a, old)
    with pytest.raises(RuntimeError, match="workbench_task_binding_mismatch"):
        release(postgres, current, other)
    release(postgres, current, old)
    fresh = task(postgres, current, wb)
    assert fresh["task_uid"] > other["task_uid"]
    assert postgres.row(f"SELECT * FROM cloud_workbench_tasks WHERE execution_id='{other['execution_id']}'") == other
    assert release(postgres, current, old)["state"] == "released"
    assert postgres.row(f"SELECT state FROM cloud_workbenches WHERE id='{wb['id']}'")["state"] == "running"


def test_live_or_unconfirmed_tasks_and_queued_runs_prevent_idle_pause(postgres, submitted):
    args, _ = submitted
    wb = running(postgres, args)
    run = claimed(postgres, args, wb)
    bound = task(postgres, run, wb)
    make_idle(postgres, wb)
    assert idle_claim(postgres, wb)["acquired"] is False
    # Lease expiry or terminal state does not prove OS descendants are dead.
    postgres.sql(f"UPDATE agent_runs SET state='failed',lease_until=clock_timestamp()-interval '1 second' WHERE id='{run['id']}'")
    assert idle_claim(postgres, wb)["acquired"] is False
    with pytest.raises(RuntimeError, match="workbench_task_cleanup_required"):
        postgres.sql(f"DELETE FROM agent_runs WHERE id='{run['id']}'")
    postgres.sql(f"UPDATE agent_runs SET lease_until=clock_timestamp()+interval '1 minute',snapshot='{{\"resource_retained\":true}}' WHERE id='{run['id']}'")
    release(postgres, run, bound)
    postgres.sql(f"UPDATE agent_runs SET snapshot='{{}}' WHERE id='{run['id']}'")
    make_idle(postgres, wb)
    queued = shared_submit(postgres, args, wb)
    assert idle_claim(postgres, wb)["acquired"] is False
    postgres.sql(f"UPDATE agent_runs SET state='failed' WHERE id='{queued['id']}'")
    paused = idle_claim(postgres, wb)
    assert paused["acquired"] and paused["workbench"]["state"] == "pausing"


def test_idle_pause_rechecks_version_activity_and_unique_copy(postgres, submitted):
    args, _ = submitted
    wb = running(postgres, args)
    assert idle_claim(postgres, wb)["acquired"] is False
    make_idle(postgres, wb)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: idle_claim(postgres, wb), range(2)))
    winner = [row for row in rows if row["acquired"]]
    assert len(winner) == 1
    pausing = winner[0]["workbench"]
    with pytest.raises(RuntimeError, match="workbench_fenced"):
        transition(postgres, wb, "retained")
    paused = transition(postgres, pausing, "paused")
    postgres.sql(f"UPDATE cloud_workbenches SET paused_at=clock_timestamp()-interval '2 days' WHERE id='{wb['id']}'")
    assert idle_claim(postgres, paused, "retire")["acquired"] is False
    assert postgres.row(f"SELECT resource FROM cloud_workbenches WHERE id='{wb['id']}'")["resource"] == wb["resource"]


def test_paused_computer_denies_task_attachment_instead_of_implicit_wakeup(postgres, submitted):
    args, _ = submitted
    wb = running(postgres, args)
    make_idle(postgres, wb)
    pausing = idle_claim(postgres, wb)["workbench"]
    paused = transition(postgres, pausing, "paused")
    run = claimed(postgres, args, wb)
    with pytest.raises(RuntimeError, match="workbench_not_running"):
        task(postgres, run, paused)
    assert postgres.row(f"SELECT state FROM cloud_workbenches WHERE id='{wb['id']}'")["state"] == "paused"


def test_task_lease_blocks_lower_level_lifecycle_rpc(postgres, submitted):
    args, _ = submitted
    wb = running(postgres, args)
    run = claimed(postgres, args, wb)
    task(postgres, run, wb)
    with pytest.raises(RuntimeError, match="workbench_has_active_occupants"):
        transition(postgres, wb, "pausing")
    with pytest.raises(RuntimeError, match="workbench_generation_in_use"):
        postgres.sql(f"UPDATE cloud_workbenches SET generation=gen_random_uuid() WHERE id='{wb['id']}'")


def test_exhausted_uid_space_never_wraps_or_reuses_an_identity(postgres, submitted):
    args, _ = submitted
    wb = running(postgres, args)
    run = claimed(postgres, args, wb)
    postgres.sql(f"UPDATE cloud_workbenches SET next_task_uid=60000 WHERE id='{wb['id']}'")
    with pytest.raises(RuntimeError, match="workbench_task_identity_exhausted"):
        task(postgres, run, wb)


def test_completed_task_history_compaction_does_not_recycle_uids(postgres, submitted):
    args, _ = submitted
    wb = running(postgres, args)
    run = claimed(postgres, args, wb)
    old = task(postgres, run, wb)
    release(postgres, run, old)
    postgres.sql(f"DELETE FROM cloud_workbench_tasks WHERE execution_id='{old['execution_id']}'")
    new_run = claimed(postgres, args, wb)
    assert task(postgres, new_run, wb)["task_uid"] == old["task_uid"] + 1


def test_new_queued_run_does_not_strand_cleanup_acknowledgement(postgres, submitted):
    args, _ = submitted
    wb = running(postgres, args)
    make_idle(postgres, wb)
    paused = transition(postgres, idle_claim(postgres, wb)["workbench"], "paused")
    # Empty fixture only: provider/save receipts are not simulated acceptance.
    postgres.sql(f"UPDATE cloud_workbenches SET unpublished_changes=false,paused_at=clock_timestamp()-interval '2 days' WHERE id='{wb['id']}'")
    cleaning = idle_claim(postgres, paused, "retire")["workbench"]
    queued = shared_submit(postgres, args, wb)
    retired = transition(postgres, cleaning, "retired")
    assert retired["resource"] is None
    fresh = call(postgres, "acquire", workbench=wb["id"], user=args["user"],
                 project=args["project"], binding=wb["binding"])["workbench"]
    assert fresh["generation"] != wb["generation"]
    assert postgres.row(f"SELECT workbench_id FROM agent_runs WHERE id='{queued['id']}'")["workbench_id"] == wb["id"]


def test_public_roles_cannot_mint_or_release_task_identities(postgres):
    for role in ["anon", "authenticated"]:
        with pytest.raises(RuntimeError, match="permission denied"):
            postgres.sql(f"SET ROLE {role}; SELECT cloud_workbench_task_acquire('{uuid4()}','{uuid4()}',1,'{uuid4()}')")
        with pytest.raises(RuntimeError, match="permission denied"):
            postgres.sql(f"SET ROLE {role}; SELECT * FROM cloud_workbench_tasks")
