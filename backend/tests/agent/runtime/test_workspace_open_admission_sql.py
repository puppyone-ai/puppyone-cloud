"""Real PostgreSQL verifies claim atomicity, identity fences and RPC permissions."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.agent.runtime.test_workbench_ownership import call, create
from tests.agent.runtime.test_workspace_readiness_operations import request

pytestmark = pytest.mark.integration


def begin(db, row, **changes):
    args = dict(
        workbench=row["id"],
        generation=row["generation"],
        user=row["user_id"],
        project=row["project_id"],
        binding=row["binding"],
        deadline=row["open_deadline"],
    )
    return call(db, "begin_open", **{**args, **changes})


def test_concurrent_dispatch_claims_once_with_phase_and_current_authority(postgres, submitted):
    args, _ = submitted
    row = request(postgres, args, create(postgres, args))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: begin(postgres, row), range(8)))
    winners = [r for r in results if r["acquired"]]
    assert len(winners) == 1
    winner = winners[0]
    assert winner["workbench"]["operation_phase"] == "allocating"
    current = call(postgres, "context", user=args["user"], project=args["project"])
    assert winner["facts"] == current["facts"]
    assert winner["revision"] == current["revision"]
    assert winner["workbench"]["operation_id"] is not None


def test_stale_deadline_generation_and_wrong_owner_cannot_claim(postgres, submitted):
    args, _ = submitted
    row = request(postgres, args, create(postgres, args))
    assert not begin(postgres, row, generation=str(uuid4()))["acquired"]
    assert not begin(postgres, row, deadline="2000-01-01T00:00:00+00:00")["acquired"]
    with pytest.raises(RuntimeError, match="workbench_unavailable"):
        begin(postgres, row, user=str(uuid4()))
    assert begin(postgres, row)["acquired"]


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_admission_not_callable_by_public_roles(postgres, role):
    with pytest.raises(RuntimeError, match="permission denied"):
        postgres.sql(
            f"SET ROLE {role}; SELECT cloud_workbench_begin_open(NULL,NULL,NULL,NULL,NULL,NULL);"
        )


def test_startup_probe_cannot_mutate_any_workspace(postgres):
    with pytest.raises(RuntimeError, match="workbench_open_claim_invalid"):
        postgres.sql("SELECT cloud_workbench_begin_open(NULL,NULL,NULL,NULL,NULL,NULL);")
