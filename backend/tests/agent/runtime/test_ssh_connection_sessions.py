"""Real PostgreSQL: one SSH connection is one Access session, each command its own connection."""

from uuid import uuid4

import pytest

from tests.agent.runtime.test_access_lifecycle import call
from tests.agent.runtime.test_agent_links import admitted, sql_call
from tests.agent.runtime.test_live_workbench_control import running

pytestmark = pytest.mark.integration


def connected(postgres, args, owner=None):
    """An Agent's connection, admitted once: its machine runs and its ticket is spent."""
    owner = owner or running(postgres, args)
    agent, workspace = admitted(postgres, args, owner)
    postgres.sql(
        "ALTER TABLE cloud_workbenches DISABLE TRIGGER USER;"
        f"""UPDATE cloud_workbenches SET resource='{{"allocation_pending":false}}'
        WHERE id='{workspace["id"]}';"""
        "ALTER TABLE cloud_workbenches ENABLE TRIGGER USER;"
    )
    session = sql_call(postgres, "access_agent_ssh_ticket", agent["id"])["id"]
    call(postgres, "workspace_ssh_grant_consume", id=session)
    workspace = postgres.row(f"SELECT * FROM cloud_workbenches WHERE id='{workspace['id']}'")
    return agent, workspace, session, owner


def command(postgres, args, workspace, session):
    context = sql_call(postgres, "cloud_workbench_context", args["user"], args["project"])
    return call(
        postgres,
        "cloud_workbench_connection_acquire_in_session",
        id=str(uuid4()),
        session=session,
        workbench=workspace["id"],
        generation=workspace["generation"],
        user=args["user"],
        project=args["project"],
        revision=context["revision"],
    )


def update(postgres, connection, action):
    return call(
        postgres,
        "cloud_workbench_connection_update",
        id=connection["id"],
        generation=connection["generation"],
        action=action,
    )


def released(postgres, connection):
    update(postgres, connection, "stop")
    return update(postgres, connection, "release")


def sessions(postgres, action, session=None):
    return call(postgres, "access_session_command", action=action, id=session)


def state(postgres, session):
    return postgres.row(f"SELECT state FROM access_sessions WHERE id='{session}'")["state"]


def revoke(postgres, args, agent):
    grant = postgres.row(f"SELECT grant_id FROM access_agent_connections WHERE id='{agent['id']}'")
    call(
        postgres,
        "access_grant_command",
        action="grant_state",
        user=args["user"],
        payload={"id": grant["grant_id"], "project_id": args["project"], "state": "revoked"},
    )


def test_one_connection_session_holds_many_commands(postgres, submitted):
    args, _ = submitted
    _, workspace, session, _ = connected(postgres, args)
    first = command(postgres, args, workspace, session)
    second = command(postgres, args, workspace, session)
    assert first["id"] != second["id"] and first["task_uid"] != second["task_uid"]
    assert first["access_session_id"] == second["access_session_id"] == session

    # A command ending leaves the session its connection still holds.
    update(postgres, first, "renew")
    assert released(postgres, first)["state"] == "released"
    assert state(postgres, session) == "active"
    assert released(postgres, second)["state"] == "released"
    assert state(postgres, session) == "active"

    # With no command running, the connection's own heartbeat keeps the session.
    before = postgres.row(f"SELECT lease_until FROM access_sessions WHERE id='{session}'")
    renewed = sessions(postgres, "renew", session)
    assert renewed["lease_until"] >= before["lease_until"]
    third = command(postgres, args, workspace, session)
    assert third["access_session_id"] == session
    released(postgres, third)

    sessions(postgres, "finish", session)
    assert state(postgres, session) == "closed"
    with pytest.raises(RuntimeError, match="access_session_ended"):
        sessions(postgres, "renew", session)


def test_a_connection_that_ends_mid_command_closes_once_the_command_is_released(
    postgres, submitted
):
    args, _ = submitted
    _, workspace, session, _ = connected(postgres, args)
    running_command = command(postgres, args, workspace, session)
    sessions(postgres, "finish", session)
    assert state(postgres, session) == "stopping"
    pending = sessions(postgres, "pending")
    assert [item["id"] for item in pending if item["access_session_id"] == session] == [
        running_command["id"]
    ]
    released(postgres, running_command)
    sessions(postgres, "pending")
    assert state(postgres, session) == "closed"


def test_revoking_an_agent_reclaims_every_command_of_its_connection(postgres, submitted):
    args, _ = submitted
    agent, workspace, session, _ = connected(postgres, args)
    first = command(postgres, args, workspace, session)
    second = command(postgres, args, workspace, session)
    revoke(postgres, args, agent)

    with pytest.raises(RuntimeError, match=r"access_session_ended|insufficient|access_denied"):
        sessions(postgres, "renew", session)
    with pytest.raises(RuntimeError, match=r"workspace_connection_expired|access_denied"):
        command(postgres, args, workspace, session)
    with pytest.raises(RuntimeError, match=r"workspace_connection_expired|access_denied"):
        update(postgres, first, "renew")

    pending = sessions(postgres, "pending")
    assert {item["id"] for item in pending if item["access_session_id"] == session} == {
        first["id"],
        second["id"],
    }
    released(postgres, first)
    sessions(postgres, "pending")
    assert state(postgres, session) == "stopping"  # One command still holds the Workspace.
    released(postgres, second)
    sessions(postgres, "pending")
    assert state(postgres, session) == "closed"


def test_an_idle_connection_that_stops_renewing_loses_its_session(postgres, submitted):
    args, _ = submitted
    _, workspace, session, _ = connected(postgres, args)
    postgres.sql(
        f"UPDATE access_sessions SET lease_until=clock_timestamp()-interval '1 second' WHERE id='{session}'"
    )
    with pytest.raises(RuntimeError, match="access_session_ended"):
        sessions(postgres, "renew", session)
    with pytest.raises(RuntimeError, match="workspace_connection_expired"):
        command(postgres, args, workspace, session)
    sessions(postgres, "pending")
    assert state(postgres, session) == "closed"


def test_a_session_cannot_be_borrowed_by_another_workspace(postgres, submitted):
    args, _ = submitted
    _, workspace, session, owner = connected(postgres, args)
    _, other, _, _ = connected(postgres, args, owner)
    with pytest.raises(RuntimeError, match="workspace_connection_expired"):
        command(postgres, args, other, session)
    assert command(postgres, args, workspace, session)["workbench_id"] == workspace["id"]


def test_the_owner_sees_whether_a_connection_is_running_a_command(postgres, submitted):
    args, _ = submitted
    _, workspace, session, _ = connected(postgres, args)

    def listed():
        rows = call(
            postgres,
            "access_inspect",
            user=args["user"],
            collection="sessions",
            project=args["project"],
        )
        return next(row for row in rows if row["id"] == session)

    # Connected and idle is not at work.
    assert listed()["commands_running"] == 0
    assert listed()["last_command_at"] is None
    running_command = command(postgres, args, workspace, session)
    assert listed()["commands_running"] == 1
    started = listed()["last_command_at"]
    released(postgres, running_command)
    assert listed()["commands_running"] == 0
    assert listed()["last_command_at"] >= started
