"""A shared Run captures its target from the locked workbench, not a caller/HEAD."""
import pytest

from tests.agent.runtime.conftest import literal
from tests.agent.runtime.test_workbench_attachment import shared_submit
from tests.agent.runtime.test_workbench_ownership import create

pytestmark = pytest.mark.integration


def test_shared_submission_captures_authoritative_target(postgres, submitted):
    args, _ = submitted
    workbench = create(postgres, args)
    run = shared_submit(postgres, args, workbench)
    assert run["policy"]["workbench_target_ref"] == workbench["target_ref"]
    assert run["workbench_id"] == workbench["id"]


def test_caller_cannot_substitute_publication_target(postgres, submitted):
    args, _ = submitted
    workbench = create(postgres, args)
    before = postgres.sql("SELECT count(*) FROM chat_sessions")
    with pytest.raises(RuntimeError, match="workbench_target_ref_mismatch"):
        shared_submit(postgres, args, workbench, policy={**args["policy"],
            "workbench_id": workbench["id"], "workbench_target_ref": "refs/heads/other"})
    assert postgres.sql("SELECT count(*) FROM chat_sessions") == before


def test_admitted_target_cannot_be_rewritten_or_removed(postgres, submitted):
    args, _ = submitted
    run = shared_submit(postgres, args, create(postgres, args))
    for patch in ["policy - 'workbench_target_ref'", "policy || '{\"workbench_target_ref\":\"refs/heads/other\"}'::jsonb"]:
        with pytest.raises(RuntimeError, match="workbench_target_ref_immutable"):
            postgres.sql(f"UPDATE agent_runs SET policy={patch} WHERE id={literal(run['id'])}")


def test_workspace_ref_change_does_not_retarget_an_admitted_run(postgres, submitted):
    args, _ = submitted
    workbench = create(postgres, args)
    run = shared_submit(postgres, args, workbench)
    postgres.sql(f"UPDATE cloud_workbenches SET target_ref='refs/heads/other' WHERE id={literal(workbench['id'])}")
    current = postgres.row(f"SELECT policy FROM agent_runs WHERE id={literal(run['id'])}")
    assert current["policy"]["workbench_target_ref"] == workbench["target_ref"]
