"""Real SQL routing: every worker claim is confined to its pool and contracts.

Each test uses its own pool, so the shared cluster's other work is invisible
to it, exactly as a hosted deployment's work is invisible to a local stack.
"""

from uuid import uuid4

import pytest

from tests.agent.runtime.conftest import claimant
from tests.agent.runtime.test_session_workspaces import BINDING
from tests.agent.runtime.test_workbench_ownership import call, create

pytestmark = pytest.mark.integration


def new_pool():
    return "test-" + uuid4().hex[:12]


def admit(db, args, pool, contract=1):
    """Submit a Run the way an API of ``pool`` admits it."""
    policy = {**args["policy"], "dispatch": {"pool": pool, "contract": contract}}
    return db.rpc("submit", **{**args, "request": str(uuid4()), "policy": policy})


def dispatch(db, name, pool, contracts=(1, 1), workbench=False):
    worker = {**claimant(name, pool=pool), "contracts": list(contracts)}
    value = db.rpc("dispatch", worker=worker, workbench=workbench)
    return value["run"] if value else None


def ownership(run):
    return dict(run=run["id"], execution=run["execution_id"], fence=run["fence"])


def last_event(db, run, kind):
    return db.row(
        f"SELECT payload FROM agent_run_events WHERE run_id='{run['id']}' AND kind='{kind}' "
        "ORDER BY sequence DESC LIMIT 1"
    )["payload"]


def test_admitted_route_is_bound_to_columns_and_removed_from_policy(postgres, submitted):
    args, legacy = submitted
    pool = new_pool()
    run = admit(postgres, args, pool, contract=3)
    assert (run["worker_pool"], run["runtime_contract"]) == (pool, 3)
    assert "dispatch" not in run["policy"]
    # An API without routing admits into the deployed default.
    assert (legacy["worker_pool"], legacy["runtime_contract"]) == ("default", 1)
    for route, error in [
        ({"pool": 5, "contract": 1}, "agent_dispatch_invalid"),
        ({"pool": pool, "contract": "1"}, "agent_dispatch_invalid"),
        ({"pool": pool}, "agent_dispatch_invalid"),
        ({"pool": "Not A Pool", "contract": 1}, "worker_pool_check"),
    ]:
        policy = {**args["policy"], "dispatch": route}
        with pytest.raises(RuntimeError, match=error):
            postgres.rpc("submit", **{**args, "request": str(uuid4()), "policy": policy})


def test_claims_are_confined_to_pool_and_contract(postgres, submitted):
    args, _ = submitted
    pool = new_pool()
    first, second = admit(postgres, args, pool, 1), admit(postgres, args, pool, 2)
    routed = {first["id"], second["id"]}
    assert dispatch(postgres, "other-pool", new_pool(), (1, 2)) is None
    # A deployed worker without routing drains only the default pool.
    for _ in range(200):
        legacy = postgres.rpc("claim", worker="deployed-worker")
        if legacy is None:
            break
        assert legacy["id"] not in routed
    else:
        pytest.fail("default pool did not drain")
    claimed = dispatch(postgres, "contract-one", pool, (1, 1))
    assert claimed["id"] == first["id"]
    assert dispatch(postgres, "contract-one", pool, (1, 1)) is None
    assert dispatch(postgres, "contract-two", pool, (1, 2))["id"] == second["id"]

    execution = postgres.row(
        f"SELECT worker FROM agent_run_executions WHERE id='{claimed['execution_id']}'"
    )["worker"]
    assert execution["pool"] == pool and execution["contracts"] == [1, 1]
    assert execution["host"] == "test" and execution["environment"] == "test"
    # Run events are user-visible: no host or environment.
    assert last_event(postgres, claimed, "execution")["worker"] == {
        "id": "contract-one",
        "pool": pool,
        "build": "test",
    }


def test_invalid_worker_identity_claims_nothing(postgres, submitted):
    pool = new_pool()
    valid = claimant("worker", pool=pool)
    for worker in [
        {},
        {**valid, "id": ""},
        {**valid, "pool": "Bad Pool"},
        {**valid, "contracts": [2, 1]},
        {**valid, "contracts": [0, 1]},
        {**valid, "contracts": [1]},
    ]:
        with pytest.raises(RuntimeError, match="agent_worker_invalid"):
            postgres.rpc("dispatch", worker=worker, workbench=False)


def test_shutdown_release_fences_at_once_and_a_peer_takes_over(postgres, submitted):
    args, _ = submitted
    pool = new_pool()
    run = admit(postgres, args, pool)
    first = dispatch(postgres, "stopping", pool)
    with pytest.raises(RuntimeError, match="agent_release_invalid"):
        postgres.rpc("release", **ownership(first), reason="bored")
    released = postgres.rpc("release", **ownership(first), reason="worker_shutdown")
    assert released["dispatch_attempts"] == 0
    with pytest.raises(RuntimeError, match="agent_execution_fenced"):
        postgres.rpc("write", **ownership(first), kind="text", payload={"delta": "late"})
    with pytest.raises(RuntimeError, match="agent_execution_fenced"):
        postgres.rpc("release", **ownership(first), reason="worker_shutdown")
    assert last_event(postgres, run, "execution_released") == {
        "execution_id": first["execution_id"],
        "fence": first["fence"],
        "reason": "worker_shutdown",
    }
    record = postgres.row(
        f"SELECT released_at IS NOT NULL AS released,release_reason FROM agent_run_executions WHERE id='{first['execution_id']}'"
    )
    assert record == {"released": True, "release_reason": "worker_shutdown"}

    successor = dispatch(postgres, "peer", pool)
    assert successor["id"] == run["id"] and successor["fence"] == first["fence"] + 1


def test_incapable_worker_never_reclaims_and_a_capable_peer_takes_over(postgres, submitted):
    args, _ = submitted
    pool = new_pool()
    run = admit(postgres, args, pool)
    first = dispatch(postgres, "incapable", pool)
    released = postgres.rpc("release", **ownership(first), reason="worker_incapable")
    assert released["dispatch_attempts"] == 1
    assert dispatch(postgres, "incapable", pool) is None
    capable = dispatch(postgres, "capable", pool)
    assert capable["id"] == run["id"] and capable["fence"] == first["fence"] + 1
    assert capable["dispatch_attempts"] == 1


def test_retained_cleanup_is_fenced_and_retried_with_backoff(postgres, submitted):
    args, _ = submitted
    pool = new_pool()
    run = admit(postgres, args, pool)

    def finish_retained(owner):
        return postgres.rpc(
            "finish",
            **ownership(owner),
            state="failed",
            code="execution_failed",
            snapshot={"resource_retained": True},
            publication=None,
            cleaned=False,
        )

    def backoff():
        return postgres.row(
            "SELECT round(extract(epoch FROM dispatch_after-clock_timestamp()))::integer AS seconds "
            f"FROM agent_runs WHERE id='{run['id']}'"
        )["seconds"]

    first = dispatch(postgres, "finisher", pool)
    finished = finish_retained(first)
    assert finished["state"] == "failed" and finished["cleanup_attempts"] == 1
    assert 40 <= backoff() <= 45
    assert last_event(postgres, run, "terminal")["worker"] == {
        "id": "finisher",
        "pool": pool,
        "build": "test",
    }
    with pytest.raises(RuntimeError, match="agent_execution_fenced"):
        postgres.rpc("renew", **ownership(first))
    assert dispatch(postgres, "cleaner", pool) is None  # Backing off.

    postgres.sql(
        f"UPDATE agent_runs SET dispatch_after=clock_timestamp()-interval '1 second' WHERE id='{run['id']}'"
    )
    retry = dispatch(postgres, "cleaner", pool)
    assert retry["id"] == run["id"] and retry["dispatch_after"] is None
    assert finish_retained(retry)["cleanup_attempts"] == 2
    assert 85 <= backoff() <= 90


def test_session_workspace_retirement_is_pool_scoped(postgres, submitted):
    args, _ = submitted
    pool = new_pool()
    admitted = admit(postgres, args, pool)
    run = dispatch(postgres, "workspace-owner", pool)
    assert run["id"] == admitted["id"]
    workspace = postgres.rpc("workspace_acquire", **ownership(run), binding=BINDING)
    assert workspace["run_id"] == run["id"]  # The acquiring Run's pool retires it.
    try:
        postgres.sql(
            "UPDATE agent_session_workspaces SET state='cleanup_pending',"
            "operation_until=clock_timestamp()-interval '1 second' "
            f"WHERE session_id='{run['session_id']}'"
        )
        sessions = lambda rows: {row["session_id"] for row in rows}  # noqa: E731
        assert run["session_id"] not in sessions(postgres.rpc("workspace_due", limit=100))
        assert run["session_id"] not in sessions(
            postgres.rpc("workspace_due_in_pool", pool=new_pool(), limit=100)
        )
        assert run["session_id"] in sessions(
            postgres.rpc("workspace_due_in_pool", pool=pool, limit=100)
        )
    finally:
        postgres.sql(
            "UPDATE agent_session_workspaces SET state='retired',retire_after=NULL,operation_until=NULL "
            f"WHERE session_id='{run['session_id']}'"
        )


def test_workbench_open_and_maintenance_are_pool_scoped(postgres, submitted):
    args, _ = submitted
    pool = new_pool()
    binding = {"provider": "third", "artifact": "pinned"}

    def request(row, function="request_open_in_pool", **route):
        return call(
            postgres,
            function,
            workbench=row["id"],
            user=args["user"],
            project=args["project"],
            binding=binding,
            revision=args["policy"]["revision"],
            **route,
        )

    def visible(function, row, *route):
        values = ",".join(f"'{value}'" for value in ("third", *route))
        return (
            postgres.sql(
                f"SELECT count(*) FROM cloud_workbench_{function}({values}) WHERE id='{row['id']}'"
            )
            == "1"
        )

    routed = request(create(postgres, args), pool=pool)
    assert routed["worker_pool"] == pool
    # Repeated clicks from another deployment never move an operation in flight.
    assert request(routed, pool=new_pool())["worker_pool"] == pool
    with pytest.raises(RuntimeError, match="workbench_pool_invalid"):
        request(create(postgres, args), pool="Bad Pool")
    assert visible("pending_in_pool", routed, pool)
    assert not visible("pending_in_pool", routed, new_pool())
    assert not visible("pending", routed)

    legacy = request(create(postgres, args), "request_open")
    assert legacy["worker_pool"] == "default"
    assert visible("pending", legacy) and not visible("pending_in_pool", legacy, pool)

    owned = call(
        postgres,
        "acquire",
        workbench=routed["id"],
        user=args["user"],
        project=args["project"],
        binding=binding,
    )["workbench"]
    assert owned["state"] != "retired"
    postgres.sql(
        "UPDATE cloud_workbenches SET maintenance_after=clock_timestamp()-interval '1 second' "
        f"WHERE id='{routed['id']}'"
    )
    assert not visible("maintenance_for_provider", routed)
    assert not visible("maintenance_in_pool", routed, new_pool())
    assert visible("maintenance_in_pool", routed, pool)
