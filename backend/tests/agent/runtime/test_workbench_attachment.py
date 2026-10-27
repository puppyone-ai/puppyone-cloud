"""Explicit attachment and old-client compatibility; no compute is allocated."""

from uuid import uuid4

import pytest

from src.platform.access.adapters.agent.runtime.control.admission import digest
from src.platform.access.adapters.agent.runtime.control.models import SubmitRun
from tests.agent.runtime.test_workbench_ownership import create


def shared_submit(postgres, args, workbench, **changes):
    return postgres.rpc(
        "submit_context",
        **{
            **args,
            "request": str(uuid4()),
            "policy": {**args["policy"], "workbench_id": workbench["id"]},
            **changes,
        },
    )


@pytest.mark.integration
def test_multiple_sessions_attach_without_claiming_legacy_resource_owner(postgres, submitted):
    args, old = submitted
    wb = create(postgres, args)
    first, second = shared_submit(postgres, args, wb), shared_submit(postgres, args, wb)
    assert first["session_id"] != second["session_id"]
    assert first["workbench_id"] == second["workbench_id"] == wb["id"]
    assert (
        postgres.row(f"SELECT workbench_id FROM chat_sessions WHERE id='{first['session_id']}'")[
            "workbench_id"
        ]
        == wb["id"]
    )
    assert (
        postgres.row(f"SELECT workbench_id FROM agent_runs WHERE id='{old['id']}'")["workbench_id"]
        is None
    )
    assert (
        postgres.sql(
            f"SELECT count(*) FROM agent_session_workspaces WHERE session_id IN ('{first['session_id']}','{second['session_id']}')"
        )
        == "0"
    )


@pytest.mark.integration
def test_old_workers_cannot_claim_shared_runs(postgres, submitted):
    args, old = submitted
    first = shared_submit(postgres, args, create(postgres, args))
    assert postgres.rpc("claim", worker="old-worker")["id"] == old["id"]
    assert postgres.rpc("claim", worker="old-worker") is None
    current = postgres.rpc("claim_workbench_execution", worker="shared-worker")
    assert current["run"]["id"] == first["id"]
    assert current["run"]["workbench_id"] == first["workbench_id"]


@pytest.mark.integration
def test_session_attachment_cannot_drift_or_silently_fall_back(postgres, submitted):
    args, _ = submitted
    first, other = create(postgres, args), create(postgres, args)
    run = shared_submit(postgres, args, first)
    postgres.sql(f"UPDATE agent_runs SET state='failed' WHERE id='{run['id']}'")
    with pytest.raises(RuntimeError, match="agent_workbench_required"):
        postgres.rpc(
            "submit_context", **{**args, "session": run["session_id"], "request": str(uuid4())}
        )
    with pytest.raises(RuntimeError, match="agent_workbench_session_mismatch"):
        shared_submit(postgres, args, other, session=run["session_id"])
    continued = shared_submit(postgres, args, first, session=run["session_id"])
    assert continued["workbench_id"] == first["id"]


@pytest.mark.integration
def test_legacy_history_cannot_be_rebound_to_a_different_computer(postgres, submitted):
    args, old = submitted
    wb = create(postgres, args)
    postgres.sql(f"UPDATE agent_runs SET state='failed' WHERE id='{old['id']}'")
    with pytest.raises(RuntimeError, match="agent_workbench_session_mismatch"):
        shared_submit(postgres, args, wb, session=old["session_id"])
    assert (
        postgres.row(f"SELECT workbench_id FROM agent_runs WHERE id='{old['id']}'")["workbench_id"]
        is None
    )


@pytest.mark.integration
def test_cross_owner_attachment_fails_inside_transaction(postgres, submitted):
    args, _ = submitted
    wb = create(postgres, args)
    postgres.sql(f"UPDATE cloud_workbenches SET user_id='{uuid4()}' WHERE id='{wb['id']}'")
    before = postgres.sql("SELECT count(*) FROM chat_sessions")
    with pytest.raises(RuntimeError, match="workbench_unavailable"):
        shared_submit(postgres, args, wb)
    assert postgres.sql("SELECT count(*) FROM chat_sessions") == before


@pytest.mark.integration
def test_shared_binding_is_immutable_and_rejects_legacy_workspace(postgres, submitted):
    args, _ = submitted
    wb = create(postgres, args)
    run = shared_submit(postgres, args, wb)
    for table, key in [("agent_runs", run["id"]), ("chat_sessions", run["session_id"])]:
        with pytest.raises(RuntimeError, match="agent_workbench_binding_immutable"):
            postgres.sql(f"UPDATE {table} SET workbench_id=NULL WHERE id='{key}'")
    current = postgres.rpc("claim_workbench_execution", worker="shared-worker")["run"]
    with pytest.raises(RuntimeError, match="agent_workbench_legacy_resource_forbidden"):
        postgres.rpc(
            "workspace_acquire",
            run=run["id"],
            execution=current["execution_id"],
            fence=current["fence"],
            binding={"provider": "docker", "artifact": "fixture"},
        )


def test_unready_composition_root_cannot_enqueue_or_fall_back():
    from types import SimpleNamespace
    from unittest.mock import Mock

    from fastapi import HTTPException

    from src.platform.access.adapters.agent.service import AgentService

    repository, admission = Mock(), Mock()
    admission.load.return_value = SimpleNamespace(receipt=None, policy={})
    service = AgentService(repository, admission)
    request = SubmitRun(
        project_id="fixture", request_id=uuid4(), workbench_id=uuid4(), prompt="test"
    )
    with pytest.raises(HTTPException) as caught:
        service.submit(str(uuid4()), request)
    assert caught.value.status_code == 503
    assert caught.value.detail == {"code": "cloud_workbench_unavailable"}
    repository.submit_run.assert_not_called()


@pytest.mark.parametrize("explicit_null", [False, True])
def test_old_request_digest_stays_byte_for_byte_compatible(explicit_null):
    from src.platform.access.adapters.agent.service import submission_digest

    wire = {
        "project_id": "fixture",
        "agent_id": None,
        "scope_id": None,
        "session_id": None,
        "request_id": str(uuid4()),
        "prompt": "unchanged request",
    }
    request = SubmitRun(**wire, **({"workbench_id": None} if explicit_null else {}))
    assert submission_digest(request) == digest(wire)
    selected = SubmitRun(**wire, workbench_id=uuid4())
    assert submission_digest(selected) != digest(wire)
