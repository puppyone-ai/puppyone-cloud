"""Real PostgreSQL acceptance for handing a workspace to an Agent with one link."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4

import asyncssh
import pytest
from fastapi import HTTPException

from src.config import settings
from src.platform.access.agent_links import AgentLinkRepository, AgentLinkService
from src.platform.access.credential_repository import access_token_hash
from src.platform.access.lifecycle_schemas import AgentConnect, AgentLinkCreate
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


@pytest.fixture
def link_service(control_api, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_CREDENTIAL_HASH_SECRET", "agent-link-test-secret")
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://api.fixture")
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://web.fixture")
    endpoint = {"host": "gateway.fixture", "port": 2222, "known_hosts": "gateway.fixture ssh-ed25519 AAAA"}
    return AgentLinkService(AgentLinkRepository(control_api), endpoint=lambda: endpoint)


def secret():
    return "pai_" + uuid4().hex * 2


def agent_request(invitation):
    # The service validates a real key; the SQL tests above bypass that layer.
    key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
    return AgentConnect(invitation=invitation, name="Test agent", public_key=key)


async def issue(service, args, row, admission):
    invitation = secret()
    await service.create(
        args["user"],
        args["project"],
        AgentLinkCreate(
            id=uuid4(), secret=invitation, workbench_id=row["id"], admission=admission
        ),
    )
    return invitation


@pytest.mark.asyncio
async def test_open_links_stay_closed_until_the_deployment_opens_them(
    postgres, submitted, link_service
):
    args, _ = submitted
    row = running(postgres, args)

    with pytest.raises(HTTPException) as refused:
        await issue(link_service, args, row, "link")
    assert refused.value.status_code == 409
    assert refused.value.detail == {"code": "agent_open_links_closed"}
    assert postgres.row(
        f"SELECT count(*) AS n FROM access_invitations WHERE admission='link' AND project_id='{args['project']}'"
    )["n"] == 0

    invitation = await issue(link_service, args, row, "sign_in")
    assert (await link_service.connect(agent_request(invitation)))["status"] == "pending"
    inventory = await link_service.inventory(args["user"], args["project"])
    assert inventory["open_links_enabled"] is False


@pytest.mark.asyncio
async def test_a_link_issued_while_open_stops_admitting_when_closed_and_works_again_reopened(
    postgres, submitted, link_service, monkeypatch
):
    args, _ = submitted
    row = running(postgres, args)
    monkeypatch.setattr(settings, "AGENT_OPEN_LINKS_ENABLED", True)
    invitation = await issue(link_service, args, row, "link")
    assert (await link_service.inventory(args["user"], args["project"]))["open_links_enabled"] is True
    admitted = (
        "SELECT count(*) AS n FROM access_agent_connections c JOIN access_invitations i "
        f"ON i.id=c.invitation_id WHERE i.project_id='{args['project']}'"
    )

    monkeypatch.setattr(settings, "AGENT_OPEN_LINKS_ENABLED", False)
    with pytest.raises(HTTPException) as closed:
        await link_service.connect(agent_request(invitation))
    with pytest.raises(HTTPException) as unknown:
        await link_service.connect(agent_request(secret()))
    # A holder cannot tell a closed link from one that never existed.
    assert closed.value.status_code == unknown.value.status_code == 403
    assert closed.value.detail == unknown.value.detail == {"code": "agent_link_unavailable"}
    assert postgres.row(admitted)["n"] == 0

    monkeypatch.setattr(settings, "AGENT_OPEN_LINKS_ENABLED", True)
    assert (await link_service.connect(agent_request(invitation)))["status"] == "ready"


def test_the_closed_check_matches_the_stored_hash_of_a_link(postgres, submitted, control_api, monkeypatch):
    args, _ = submitted
    row = running(postgres, args)
    monkeypatch.setattr(settings, "ACCESS_CREDENTIAL_HASH_SECRET", "agent-link-test-secret")
    invitation = secret()
    create_key = access_token_hash(invitation)
    link_command(
        postgres, "create", args["user"], id=str(uuid4()), project_id=args["project"],
        workbench_id=row["id"], admission="link", key_hash=create_key,
    )
    sign_in = create_link(postgres, args, row, "sign_in")
    repository = AgentLinkRepository(control_api)
    assert repository.admits_without_sign_in([create_key]) is True
    assert repository.admits_without_sign_in([sign_in]) is False
    assert repository.admits_without_sign_in([hash_of()]) is False


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
