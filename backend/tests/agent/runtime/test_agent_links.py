"""Real PostgreSQL acceptance for handing a workspace to an Agent with one link."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4

import pytest

from tests.agent.runtime.conftest import literal
from tests.agent.runtime.test_access_lifecycle import call
from tests.agent.runtime.test_live_workbench_control import running

pytestmark = pytest.mark.integration

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOV/agentlinkagentlinkagentlinkagentlink"


def hash_of():
    return uuid4().hex * 2


def link_command(db, action, user, **payload):
    return call(db, "access_agent_link_command", action=action, user=user, payload=payload)


def sql_call(db, name, *values):
    rendered = ",".join(array(value) if isinstance(value, list) else literal(value) for value in values)
    output = db.sql(f"SELECT to_jsonb(public.{name}({rendered}));")
    return json.loads(output) if output else None


def array(values):
    return "ARRAY[" + ",".join(literal(value) for value in values) + "]::text[]"


def create_link(db, args, row, admission):
    secret = hash_of()
    link_command(
        db,
        "create",
        args["user"],
        id=str(uuid4()),
        project_id=args["project"],
        workbench_id=row["id"],
        admission=admission,
        key_hash=secret,
    )
    return secret


def connect(db, secret, poll=None, key=KEY):
    poll = poll or hash_of()
    return sql_call(db, "access_agent_connect", [secret], poll, "Test agent", key), poll


def status(db, connection, poll):
    return sql_call(db, "access_agent_connection_status", connection, [poll])


def stranger(db):
    user = str(uuid4())
    db.sql(f"INSERT INTO auth.users(id) VALUES('{user}');")
    return user


def test_sign_in_link_waits_for_the_owner_and_then_keeps_working(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    link = create_link(postgres, args, row, "sign_in")

    connection, poll = connect(postgres, link)
    assert connection["state"] == "pending"
    identity = connection["id"]
    assert status(postgres, identity, poll)["state"] == "pending"
    assert sql_call(postgres, "access_agent_ssh_admit", identity, KEY) is False

    # The URL an Agent passes on is safe to leak: only the owner can act on it.
    other = stranger(postgres)
    for action, payload in (("review", {}), ("decide", {"decision": "approve"})):
        with pytest.raises(RuntimeError, match="access_denied"):
            link_command(postgres, action, other, id=identity, **payload)

    review = link_command(postgres, "review", args["user"], id=identity)
    assert review["name"] == "Test agent"
    assert review["state"] == "pending"
    assert "public_key" not in review and "poll_hash" not in review

    decided = link_command(postgres, "decide", args["user"], id=identity, decision="approve")
    assert decided["state"] == "approved"
    # A double click is one approval.
    link_command(postgres, "decide", args["user"], id=identity, decision="approve")
    assert postgres.row(f"SELECT approvals FROM access_invitations WHERE key_hash='{link}'")["approvals"] == 1

    assert status(postgres, identity, poll)["state"] == "approved"
    assert sql_call(postgres, "access_agent_ssh_admit", identity, KEY) is True
    assert sql_call(postgres, "access_agent_ssh_admit", identity, KEY + "x") is False

    # Each connection is its own one-use ticket under the existing session path.
    first = sql_call(postgres, "access_agent_ssh_ticket", identity)["id"]
    second = sql_call(postgres, "access_agent_ssh_ticket", identity)["id"]
    assert first != second
    consumed = call(postgres, "workspace_ssh_grant_consume", id=first)
    assert consumed["public_key"] == KEY
    assert consumed["generation"] == row["generation"]
    assert consumed["user_id"] == args["user"]
    with pytest.raises(RuntimeError, match="ssh_grant_unavailable"):
        call(postgres, "workspace_ssh_grant_consume", id=first)
    assert postgres.row(f"SELECT state FROM access_sessions WHERE id='{first}'")["state"] == "active"

    # The inventory never exposes what would let someone else connect.
    inventory = link_command(postgres, "inventory", args["user"], project_id=args["project"])
    assert [item["name"] for item in inventory["connections"]] == ["Test agent"]
    assert "key_hash" not in json.dumps(inventory) and KEY not in json.dumps(inventory)

    grant = inventory["connections"][0]["grant_id"]
    call(
        postgres,
        "access_grant_command",
        action="grant_state",
        user=args["user"],
        payload={"id": grant, "project_id": args["project"], "state": "revoked"},
    )
    assert status(postgres, identity, poll)["state"] == "revoked"
    assert sql_call(postgres, "access_agent_ssh_admit", identity, KEY) is False
    with pytest.raises(RuntimeError, match="access_denied"):
        sql_call(postgres, "access_agent_ssh_ticket", identity)
    assert postgres.row(f"SELECT state FROM access_sessions WHERE id='{first}'")["state"] == "stopping"
    assert link_command(postgres, "inventory", args["user"], project_id=args["project"])["connections"] == []


def test_a_link_admits_any_agent_and_stopping_sharing_ends_every_one(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    link = create_link(postgres, args, row, "link")

    (first, first_poll), (second, _) = connect(postgres, link), connect(postgres, link)
    assert first["state"] == second["state"] == "approved"
    assert first["id"] != second["id"]
    assert sql_call(postgres, "access_agent_ssh_admit", first["id"], KEY) is True

    grant = postgres.row(
        f"""SELECT g.expires_at,g.approved_by,i.expires_at AS link_expires_at,g.capabilities
        FROM access_agent_connections c JOIN access_grants g ON g.id=c.grant_id
        JOIN access_invitations i ON i.id=c.invitation_id WHERE c.id='{first['id']}'"""
    )
    assert grant["approved_by"] == args["user"]
    assert "workspace.wake" in grant["capabilities"]
    assert datetime.fromisoformat(grant["expires_at"]) <= datetime.fromisoformat(grant["link_expires_at"])

    link_command(postgres, "stop", args["user"], project_id=args["project"])
    assert status(postgres, first["id"], first_poll)["state"] == "revoked"
    assert sql_call(postgres, "access_agent_ssh_admit", second["id"], KEY) is False
    with pytest.raises(RuntimeError, match="access_denied"):
        connect(postgres, link)
    inventory = link_command(postgres, "inventory", args["user"], project_id=args["project"])
    assert inventory == {"links": [], "connections": []}


def test_only_the_workspace_owner_can_create_a_link_and_a_client_cannot_widen_it(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    with pytest.raises(RuntimeError, match="access_denied"):
        link_command(
            postgres,
            "create",
            stranger(postgres),
            id=str(uuid4()),
            project_id=args["project"],
            workbench_id=row["id"],
            admission="link",
            key_hash=hash_of(),
        )
    with pytest.raises(RuntimeError, match="access_invalid_request"):
        link_command(
            postgres,
            "create",
            args["user"],
            id=str(uuid4()),
            project_id=args["project"],
            workbench_id=row["id"],
            admission="approval",
            key_hash=hash_of(),
        )
    link = create_link(postgres, args, row, "sign_in")
    stored = postgres.row(f"SELECT * FROM access_invitations WHERE key_hash='{link}'")
    assert stored["channels"] == ["ssh"]
    assert stored["max_grant_seconds"] == 2592000
    assert stored["target_kind"] == "workspace"


def test_an_ordinary_invitation_is_not_an_agent_link(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    secret = hash_of()
    call(
        postgres,
        "access_grant_command",
        action="invite",
        user=args["user"],
        payload={
            "id": str(uuid4()),
            "key_hash": secret,
            "project_id": args["project"],
            "target_kind": "workspace",
            "workbench_id": row["id"],
            "capabilities": ["workspace.read", "workspace.write", "workspace.shell"],
            "channels": ["ssh"],
            "expires_at": _tomorrow(postgres),
        },
    )
    with pytest.raises(RuntimeError, match="access_denied"):
        connect(postgres, secret)


def _tomorrow(db):
    return db.row("SELECT clock_timestamp()+interval '1 day' AS tomorrow")["tomorrow"]


def test_an_abandoned_request_cannot_be_approved_later(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    link = create_link(postgres, args, row, "sign_in")
    connection, poll = connect(postgres, link)
    postgres.sql(
        f"""UPDATE access_agent_connections SET created_at=clock_timestamp()-interval '1 hour',
        expires_at=clock_timestamp()-interval '1 minute' WHERE id='{connection['id']}';"""
    )
    assert status(postgres, connection["id"], poll)["state"] == "expired"
    with pytest.raises(RuntimeError, match="access_conflict"):
        link_command(postgres, "decide", args["user"], id=connection["id"], decision="approve")


def test_rejection_is_final(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    link = create_link(postgres, args, row, "sign_in")
    connection, poll = connect(postgres, link)
    link_command(postgres, "decide", args["user"], id=connection["id"], decision="reject")
    assert status(postgres, connection["id"], poll)["state"] == "rejected"
    with pytest.raises(RuntimeError, match="access_conflict"):
        link_command(postgres, "decide", args["user"], id=connection["id"], decision="approve")
    assert sql_call(postgres, "access_agent_ssh_admit", connection["id"], KEY) is False


def test_a_leaked_link_cannot_flood_its_owner(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    link = create_link(postgres, args, row, "sign_in")
    for _ in range(20):
        connect(postgres, link)
    with pytest.raises(RuntimeError, match="access_conflict"):
        connect(postgres, link)


def test_the_polling_secret_belongs_to_one_connection(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    link = create_link(postgres, args, row, "sign_in")
    connection, _ = connect(postgres, link)
    assert status(postgres, connection["id"], hash_of()) == {"error": "access_invalid_credential"}


def test_a_stopped_workspace_needs_its_wake_capability(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    connection, _ = connect(postgres, create_link(postgres, args, row, "link"))
    postgres.sql(f"ALTER TABLE cloud_workbenches DISABLE TRIGGER USER; UPDATE cloud_workbenches SET state='paused' WHERE id='{row['id']}'; ALTER TABLE cloud_workbenches ENABLE TRIGGER USER;")
    # Agent links carry wake, so a paused workspace still issues a ticket.
    assert sql_call(postgres, "access_agent_ssh_ticket", connection["id"])["id"]
    postgres.sql(f"ALTER TABLE cloud_workbenches DISABLE TRIGGER USER; UPDATE cloud_workbenches SET state='retained' WHERE id='{row['id']}'; ALTER TABLE cloud_workbenches ENABLE TRIGGER USER;")
    with pytest.raises(RuntimeError, match="access_workspace_unavailable"):
        sql_call(postgres, "access_agent_ssh_ticket", connection["id"])


def test_concurrent_approvals_create_one_grant(postgres, submitted):
    args, _ = submitted
    row = running(postgres, args)
    connection, _ = connect(postgres, create_link(postgres, args, row, "sign_in"))

    def approve(_):
        return link_command(postgres, "decide", args["user"], id=connection["id"], decision="approve")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(approve, range(4)))
    assert {item["state"] for item in results} == {"approved"}
    grants = postgres.row(
        f"""SELECT count(*) AS total FROM access_grants g JOIN access_requests r ON r.id=g.request_id
        JOIN access_agent_connections c ON c.invitation_id=r.invitation_id WHERE c.id='{connection['id']}'"""
    )
    assert grants["total"] == 1
