"""Real PostgreSQL 17 tests for pluggable multi-agent collaboration routing."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

from .conftest import NativeDatabase, literal, make_running_workbench, seed_project

ROOT = Path(__file__).resolve().parents[5]
MIGRATION = ROOT / "supabase/migrations/20261124010000_expand_agent_collaboration_router.sql"
KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOV/sharedagentkeyAsharedagentkeyAshareda"
KEY_B = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOV/sharedagentkeyBsharedagentkeyBsharedb"


def _array(values: list[str]) -> str:
    return "ARRAY[" + ",".join(literal(v) for v in values) + "]::text[]"


def _sql_call(pg: NativeDatabase, name: str, *values):
    rendered = ",".join(
        _array(v) if isinstance(v, list) else literal(v) for v in values
    )
    output = pg.sql(f"SELECT to_jsonb(public.{name}({rendered}));")
    return json.loads(output) if output else None


def _admit_agent(
    pg: NativeDatabase,
    admin_user: str,
    project: str,
    workbench_id: str,
    *,
    name: str = "Agent",
    key: str = KEY_A,
) -> dict:
    link_secret = uuid4().hex * 2
    pg.call_rpc(
        "access_agent_link_command",
        action="create",
        user=admin_user,
        payload={
            "id": str(uuid4()),
            "project_id": project,
            "workbench_id": workbench_id,
            "admission": "link",
            "key_hash": link_secret,
        },
    )
    conn = _sql_call(pg, "access_agent_connect", [link_secret], uuid4().hex * 2, name, key)
    assert conn["state"] == "approved"
    return conn


def _open_command(
    pg: NativeDatabase,
    *,
    session_id: str,
    workbench: dict,
    user: str,
    project: str,
) -> dict:
    ctx = pg.wb_rpc("context", user=user, project=project)
    return pg.wb_rpc(
        "connection_acquire_in_session",
        id=str(uuid4()),
        session=session_id,
        workbench=workbench["id"],
        generation=workbench["generation"],
        user=user,
        project=project,
        revision=ctx["revision"],
    )


def test_shared_machine_routes_multiple_agents_into_project_default_workspace(pg):
    env = seed_project(pg)
    shared = make_running_workbench(pg, env["owner"], env["project"])

    agent_a = _admit_agent(pg, env["owner"], env["project"], shared["id"], name="Grok", key=KEY_A)
    agent_b = _admit_agent(pg, env["owner"], env["project"], shared["id"], name="Claude", key=KEY_B)

    wb_a = _sql_call(pg, "access_agent_workspace", agent_a["id"], "shared_machine")
    wb_b = _sql_call(pg, "access_agent_workspace", agent_b["id"], "shared_machine")

    assert wb_a["id"] == wb_b["id"] == shared["id"]
    assert wb_a["is_default"] is True
    assert wb_a["target_ref"] == "refs/heads/workspace"
    assert wb_a["collaboration_mode"] == "shared_machine"
    assert wb_a["actor_user_id"] == env["owner"]

    # Zero separate per-Agent workbenches created in shared_machine mode.
    ext_count = pg.row(
        f"SELECT count(*) AS n FROM cloud_workbenches WHERE project_id='{env['project']}' "
        "AND agent_registration_id IS NOT NULL"
    )["n"]
    assert ext_count == 0

    # Both Agents mint tickets, open sessions, and run concurrent shells on the SAME default VM.
    ticket_a = _sql_call(pg, "access_agent_ssh_ticket", agent_a["id"], "shared_machine")["id"]
    ticket_b = _sql_call(pg, "access_agent_ssh_ticket", agent_b["id"], "shared_machine")["id"]
    consumed_a = pg.call_rpc("workspace_ssh_grant_consume", id=ticket_a)
    consumed_b = pg.call_rpc("workspace_ssh_grant_consume", id=ticket_b)
    assert consumed_a["workbench_id"] == consumed_b["workbench_id"] == shared["id"]

    cmd_a = _open_command(
        pg, session_id=ticket_a, workbench=shared, user=env["owner"], project=env["project"]
    )
    cmd_b = _open_command(
        pg, session_id=ticket_b, workbench=shared, user=env["owner"], project=env["project"]
    )
    assert cmd_a["workbench_id"] == cmd_b["workbench_id"] == shared["id"]
    assert cmd_a["task_uid"] != cmd_b["task_uid"]

    # Shared workspace records unpublished changes so periodic/pause backup captures Agent edits.
    assert (
        pg.row(f"SELECT unpublished_changes FROM cloud_workbenches WHERE id='{shared['id']}'")[
            "unpublished_changes"
        ]
        is True
    )

    # Inventory reports exact top-level keys and shared workspace branch.
    inv = pg.call_rpc(
        "access_agent_link_command",
        action="inventory",
        user=env["owner"],
        payload={"project_id": env["project"]},
    )
    assert set(inv.keys()) == {"links", "connections", "pending"}
    assert {item["name"] for item in inv["connections"]} == {"Grok", "Claude"}
    assert {item["branch"] for item in inv["connections"]} == {"refs/heads/workspace"}
    assert {item["collaboration_mode"] for item in inv["connections"]} == {"shared_machine"}

    # Revoking ONLY Agent A stops Agent A's session/commands while Agent B keeps running!
    grant_a = pg.row(
        f"SELECT grant_id FROM access_agent_connections WHERE id='{agent_a['id']}'"
    )["grant_id"]
    pg.call_rpc(
        "access_grant_command",
        action="grant_state",
        user=env["owner"],
        payload={"id": grant_a, "project_id": env["project"], "state": "revoked"},
    )
    assert _sql_call(pg, "access_agent_ssh_admit", agent_a["id"], KEY_A) is False
    with pytest.raises(RuntimeError, match="access_denied"):
        _sql_call(pg, "access_agent_ssh_ticket", agent_a["id"], "shared_machine")
    with pytest.raises(RuntimeError, match=r"workspace_connection_expired|access_denied"):
        pg.wb_rpc(
            "connection_update",
            id=cmd_a["id"],
            generation=shared["generation"],
            action="renew",
        )
    pending = pg.call_rpc("access_session_command", action="pending")
    pending_ids = {item["id"] for item in pending}
    assert cmd_a["id"] in pending_ids
    assert cmd_b["id"] not in pending_ids

    # Agent B continues renewing cleanly on the running shared default workspace.
    renewed_b = pg.wb_rpc(
        "connection_update",
        id=cmd_b["id"],
        generation=shared["generation"],
        action="renew",
    )
    assert renewed_b["state"] == "active"
    assert pg.row(f"SELECT state FROM cloud_workbenches WHERE id='{shared['id']}'")["state"] == (
        "running"
    )


def test_shared_machine_enforces_five_agent_seats_and_frees_on_revocation(pg):
    env = seed_project(pg)
    shared = make_running_workbench(pg, env["owner"], env["project"])

    agents = []
    for index in range(5):
        conn = _admit_agent(
            pg, env["owner"], env["project"], shared["id"], name=f"Agent-{index}", key=KEY_A
        )
        wb = _sql_call(pg, "access_agent_workspace", conn["id"], "shared_machine")
        assert wb["id"] == shared["id"]
        agents.append(conn)

    # 6th Agent hits the 5-seat limit.
    sixth = _admit_agent(pg, env["owner"], env["project"], shared["id"], name="Agent-6", key=KEY_B)
    with pytest.raises(RuntimeError, match="workspace_agent_seat_limit"):
        _sql_call(pg, "access_agent_workspace", sixth["id"], "shared_machine")

    # Already seated Agent #0 can still reconnect.
    assert _sql_call(pg, "access_agent_workspace", agents[0]["id"], "shared_machine")["id"] == (
        shared["id"]
    )

    # Revoking Agent #0 frees its seat immediately so the 6th Agent can connect.
    grant_0 = pg.row(
        f"SELECT grant_id FROM access_agent_connections WHERE id='{agents[0]['id']}'"
    )["grant_id"]
    pg.call_rpc(
        "access_grant_command",
        action="grant_state",
        user=env["owner"],
        payload={"id": grant_0, "project_id": env["project"], "state": "revoked"},
    )
    assert _sql_call(pg, "access_agent_workspace", sixth["id"], "shared_machine")["id"] == (
        shared["id"]
    )


def test_multi_admin_shared_workspace_agent_survives_creator_demotion(pg):
    env = seed_project(
        pg,
        extra_members=[("first_editor", "editor"), ("second_admin", "admin")],
    )
    # first_editor opens the shared default workspace first (so w.user_id = first_editor),
    # then is demoted to viewer while second_admin invites and approves an Agent.
    shared = make_running_workbench(pg, env["first_editor"], env["project"])
    assert shared["user_id"] == env["first_editor"]

    pg.sql(
        f"UPDATE public.project_members SET role='viewer' "
        f"WHERE project_id='{env['project']}' AND user_id='{env['first_editor']}';"
    )
    agent = _admit_agent(
        pg, env["second_admin"], env["project"], shared["id"], name="SecondAdminBot", key=KEY_A
    )
    wb = _sql_call(pg, "access_agent_workspace", agent["id"], "shared_machine")
    assert wb["id"] == shared["id"]
    assert wb["actor_user_id"] == env["second_admin"]

    ticket = _sql_call(pg, "access_agent_ssh_ticket", agent["id"], "shared_machine")["id"]
    consumed = pg.call_rpc("workspace_ssh_grant_consume", id=ticket)
    assert consumed["user_id"] == env["second_admin"]
    assert consumed["workbench_id"] == shared["id"]

    cmd = _open_command(
        pg,
        session_id=ticket,
        workbench=shared,
        user=env["second_admin"],
        project=env["project"],
    )
    assert cmd["workbench_id"] == shared["id"]
    assert cmd["user_id"] == env["second_admin"]


def test_project_override_legacy_arity_and_registered_credential_overloads(pg):
    env = seed_project(pg)
    shared = make_running_workbench(pg, env["owner"], env["project"])

    # 1-arg compatibility overload defaults to isolated_branch when projects.agent_collab_mode is NULL.
    legacy_agent = _admit_agent(
        pg, env["owner"], env["project"], shared["id"], name="Legacy", key=KEY_A
    )
    legacy_wb = _sql_call(pg, "access_agent_workspace", legacy_agent["id"])
    assert legacy_wb["id"] != shared["id"]
    assert legacy_wb["is_default"] is False
    assert legacy_wb["target_ref"].startswith("refs/heads/agents/")

    # Invalid mode fails closed.
    with pytest.raises(RuntimeError, match="access_invalid_request"):
        _sql_call(pg, "access_agent_workspace", legacy_agent["id"], "daytona")

    # Project-level override switches even 1-arg callers to shared_machine.
    pg.sql(
        f"UPDATE public.projects SET agent_collab_mode='shared_machine' WHERE id='{env['project']}';"
    )
    routed = _sql_call(pg, "access_agent_workspace", legacy_agent["id"])
    assert routed["id"] == shared["id"]

    # Project-level override to isolated_branch overrides caller 'shared_machine'.
    pg.sql(
        f"UPDATE public.projects SET agent_collab_mode='isolated_branch' WHERE id='{env['project']}';"
    )
    pinned = _sql_call(pg, "access_agent_workspace", legacy_agent["id"], "shared_machine")
    assert pinned["id"] == legacy_wb["id"]
    pg.sql(f"UPDATE public.projects SET agent_collab_mode=NULL WHERE id='{env['project']}';")

    # Verify both 9-arg and 10-arg access_issue_credential overloads have zero ambiguity.
    reg_id = str(uuid4())
    key1 = uuid4().hex * 2
    key2 = uuid4().hex * 2
    key3 = uuid4().hex * 2
    pg.call_rpc(
        "access_registration_command",
        action="create",
        user=env["owner"],
        payload={"id": reg_id, "name": "CLI Bot", "key_hash": key1},
    )
    inv_id = str(uuid4())
    inv_hash = uuid4().hex * 2
    expires_at = pg.row("SELECT (clock_timestamp() + interval '7 days') AS ts")["ts"]
    pg.call_rpc(
        "access_grant_command",
        action="invite",
        user=env["owner"],
        payload={
            "id": inv_id,
            "project_id": env["project"],
            "key_hash": inv_hash,
            "target_kind": "workspace",
            "workbench_id": shared["id"],
            "capabilities": ["workspace.read", "workspace.write", "workspace.shell"],
            "channels": ["ssh"],
            "expires_at": expires_at,
        },
    )
    req_id = str(uuid4())
    pg.call_rpc(
        "access_grant_command",
        action="request",
        user=env["owner"],
        payload={"id": req_id, "registration_id": reg_id, "key_hash": inv_hash},
    )
    grant = pg.call_rpc(
        "access_grant_command",
        action="decide",
        user=env["owner"],
        payload={
            "id": req_id,
            "project_id": env["project"],
            "decision": "approve",
            "expires_at": expires_at,
        },
    )
    reg_wb = _sql_call(pg, "access_registered_workspace", [key1], grant["id"], "shared_machine")
    assert reg_wb["id"] == shared["id"]
    assert reg_wb["collaboration_mode"] == "shared_machine"

    # 10-arg overload routes to shared_machine.
    issued_10 = _sql_call(
        pg,
        "access_issue_credential",
        [key1],
        key2,
        str(uuid4()),
        grant["id"],
        "ssh",
        None,
        KEY_A,
        None,
        900,
        "shared_machine",
    )
    assert issued_10["workbench_id"] == shared["id"]

    # 9-arg legacy overload still resolves unambiguously (defaults to isolated_branch).
    # First mark the isolated workspace running so 9-arg issue succeeds.
    iso_wb = _sql_call(pg, "access_registered_workspace", [key2], grant["id"])
    assert iso_wb["id"] != shared["id"]
    pg.sql(
        "ALTER TABLE cloud_workbenches DISABLE TRIGGER USER;"
        f"UPDATE cloud_workbenches SET state='running' WHERE id='{iso_wb['id']}';"
        "ALTER TABLE cloud_workbenches ENABLE TRIGGER USER;"
    )
    issued_9 = _sql_call(
        pg,
        "access_issue_credential",
        [key2],
        key3,
        str(uuid4()),
        grant["id"],
        "ssh",
        None,
        KEY_A,
        None,
        900,
    )
    assert issued_9["workbench_id"] == iso_wb["id"]


def test_populated_upgrade_rollback_dry_run_and_function_privileges(pg):
    spec = importlib.util.spec_from_file_location(
        "agent_collab_upgrade_pg", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    database = "agent_collab_upgrade_" + uuid4().hex
    pg.sql(f"CREATE DATABASE {database}")
    scratch = NativeDatabase(pg.url.rsplit("/", 1)[0] + "/" + database)
    try:
        scratch.sql((ROOT / "scripts/testing/postgres_auth_stub.sql").read_text(encoding="utf-8"))
        for path in sorted((ROOT / "supabase/migrations").glob("*.sql")):
            if path.name >= MIGRATION.name:
                break
            scratch.sql(module.product_migration_sql(path))

        env = seed_project(scratch)
        shared = make_running_workbench(scratch, env["owner"], env["project"])
        existing_agent = _admit_agent(
            scratch, env["owner"], env["project"], shared["id"], name="PreUpgradeBot", key=KEY_A
        )
        old_wb = _sql_call(scratch, "access_agent_workspace", existing_agent["id"])
        assert old_wb["id"] != shared["id"]

        # Rollback dry run leaves schema untouched.
        scratch.sql(MIGRATION.read_text(encoding="utf-8").replace("COMMIT;", "ROLLBACK;"))
        assert (
            scratch.row(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='projects' AND column_name='agent_collab_mode'"
            )
            is None
        )

        # Apply Expand migration for real on the populated database.
        scratch.sql(MIGRATION.read_text(encoding="utf-8"))

        # Pre-upgrade Agent transitions cleanly into shared_machine on next connect.
        upgraded_wb = _sql_call(
            scratch, "access_agent_workspace", existing_agent["id"], "shared_machine"
        )
        assert upgraded_wb["id"] == shared["id"]

        # Privilege containment: anon/authenticated have ZERO execute privileges;
        # internal helpers are revoked from service_role too.
        public_rpcs = [
            "public.access_agent_workspace(uuid,text)",
            "public.access_registered_workspace(text[],uuid,text)",
            "public.access_agent_ssh_ticket(uuid,text)",
            "public.access_issue_credential(text[],text,uuid,uuid,text,text,text,text,integer,text)",
        ]
        internal_helpers = [
            "public._cloud_workbench_resolve_collab_mode(text,text)",
            "public._cloud_workbench_agent_seat_count(text)",
            "public._cloud_workbench_shared_default(uuid,text)",
            "public.access_grant_agent_workspace(uuid,text)",
        ]
        for sig in public_rpcs:
            perms = scratch.row(
                f"SELECT has_function_privilege('anon','{sig}','EXECUTE') AS anon, "
                f"has_function_privilege('authenticated','{sig}','EXECUTE') AS auth, "
                f"has_function_privilege('service_role','{sig}','EXECUTE') AS svc"
            )
            assert perms == {"anon": False, "auth": False, "svc": True}, sig
        for sig in internal_helpers:
            perms = scratch.row(
                f"SELECT has_function_privilege('anon','{sig}','EXECUTE') AS anon, "
                f"has_function_privilege('authenticated','{sig}','EXECUTE') AS auth, "
                f"has_function_privilege('service_role','{sig}','EXECUTE') AS svc"
            )
            assert perms == {"anon": False, "auth": False, "svc": False}, sig

        # Existing cloud_agent_contracts.inc passes cleanly.
        scratch.sql(
            (ROOT / "supabase/tests/_support/cloud_agent_contracts.inc").read_text(encoding="utf-8")
        )
    finally:
        pg.sql(f"DROP DATABASE {database} WITH (FORCE)")


def test_tier2_fly_host_export_routes_separate_agent_vms_linked_to_host_and_guards_idle_pause(pg):
    env = seed_project(pg)
    host = make_running_workbench(pg, env["owner"], env["project"])
    # Opt this Project into Tier 2 (fly_host_export) via per-project override.
    pg.sql(f"UPDATE public.projects SET agent_collab_mode='fly_host_export' WHERE id='{env['project']}'")

    agent_a = _admit_agent(pg, env["owner"], env["project"], host["id"], name="Tier2A", key=KEY_A)
    agent_b = _admit_agent(pg, env["owner"], env["project"], host["id"], name="Tier2B", key=KEY_B)

    wb_a = _sql_call(pg, "access_agent_workspace", agent_a["id"], "shared_machine")
    wb_b = _sql_call(pg, "access_agent_workspace", agent_b["id"], "shared_machine")

    # Project override wins: each Agent gets its OWN isolated compute workbench,
    # BOTH linked to the Project's single shared default Host Workspace on refs/heads/workspace.
    assert wb_a["id"] != wb_b["id"]
    assert wb_a["id"] != host["id"] and wb_b["id"] != host["id"]
    assert wb_a["host_workbench_id"] == host["id"]
    assert wb_b["host_workbench_id"] == host["id"]
    assert wb_a["collaboration_mode"] == "fly_host_export"
    assert wb_b["collaboration_mode"] == "fly_host_export"
    assert wb_a["target_ref"] == "refs/heads/workspace"
    assert wb_b["target_ref"] == "refs/heads/workspace"

    # Inventory reports refs/heads/workspace and fly_host_export for both Agents.
    inv = pg.call_rpc(
        "access_agent_link_command",
        action="inventory",
        user=env["owner"],
        payload={"project_id": env["project"]},
    )
    assert set(inv.keys()) == {"links", "connections", "pending"}
    by_id = {c["id"]: c for c in inv["connections"]}
    assert by_id[agent_a["id"]]["collaboration_mode"] == "fly_host_export"
    assert by_id[agent_a["id"]]["branch"] == "refs/heads/workspace"
    assert by_id[agent_b["id"]]["collaboration_mode"] == "fly_host_export"
    assert by_id[agent_b["id"]]["branch"] == "refs/heads/workspace"

    # Bring Agent A's compute workbench to running and verify Project Host idle pause is deferred!
    pg.sql(
        f"UPDATE public.cloud_workbenches SET state='running', compute_epoch=1, "
        f"binding='{{\"provider\":\"fly\",\"artifact\":\"img\"}}'::jsonb, "
        f"resource='{{\"app\":\"pw-host\",\"resource_id\":\"mach-a\",\"allocation_pending\":false}}'::jsonb "
        f"WHERE id='{wb_a['id']}'"
    )
    pg.sql(
        f"UPDATE public.cloud_workbenches SET last_activity_at=clock_timestamp() - interval '2 hours', "
        f"unpublished_changes=false, backup_until=NULL WHERE id='{host['id']}'"
    )
    stale_host = pg.row(f"SELECT id, generation, version FROM cloud_workbenches WHERE id='{host['id']}'")
    blocked = pg.wb_rpc(
        "idle_claim",
        workbench=stale_host["id"],
        generation=stale_host["generation"],
        version=stale_host["version"],
        action="pause",
    )
    assert blocked["acquired"] is False

    # Running a command on Agent A propagates activity + unpublished_changes to Project Host VM!
    ticket_a = _sql_call(pg, "access_agent_ssh_ticket", agent_a["id"], "fly_host_export")["id"]
    pg.call_rpc("workspace_ssh_grant_consume", id=ticket_a)
    wb_a_running = pg.row(f"SELECT * FROM cloud_workbenches WHERE id='{wb_a['id']}'")
    cmd_a = _open_command(
        pg, session_id=ticket_a, workbench=wb_a_running, user=env["owner"], project=env["project"]
    )
    assert cmd_a["workbench_id"] == wb_a["id"]
    host_after = pg.row(
        f"SELECT unpublished_changes, last_activity_at > clock_timestamp() - interval '1 minute' AS fresh "
        f"FROM cloud_workbenches WHERE id='{host['id']}'"
    )
    assert host_after == {"unpublished_changes": True, "fresh": True}

    # Once Agent A pauses (and host is idle), idle_claim allows pausing Host VM cleanly.
    pg.wb_rpc("connection_update", id=cmd_a["id"], generation=cmd_a["generation"], action="stop")
    pg.wb_rpc("connection_update", id=cmd_a["id"], generation=cmd_a["generation"], action="release")
    pg.sql(f"UPDATE public.cloud_workbenches SET state='paused' WHERE id='{wb_a['id']}'")
    pg.sql(
        f"UPDATE public.cloud_workbenches SET last_activity_at=clock_timestamp() - interval '2 hours', "
        f"backup_until=NULL WHERE id='{host['id']}'"
    )
    stale_host2 = pg.row(f"SELECT id, generation, version FROM cloud_workbenches WHERE id='{host['id']}'")
    allowed = pg.wb_rpc(
        "idle_claim",
        workbench=stale_host2["id"],
        generation=stale_host2["generation"],
        version=stale_host2["version"],
        action="pause",
    )
    assert allowed["acquired"] is True

    # Privilege containment on updated functions from 20261124020000 migration.
    for sig in (
        "public.cloud_workbench_idle_claim(uuid,uuid,bigint,text)",
        "public.cloud_workbench_connection_acquire_in_session(uuid,uuid,uuid,uuid,uuid,text,jsonb)",
        "public.cloud_workbench_connection_update(uuid,uuid,text)",
        "public.access_agent_link_command(text,uuid,jsonb)",
    ):
        perms = pg.row(
            f"SELECT has_function_privilege('anon','{sig}','EXECUTE') AS anon, "
            f"has_function_privilege('authenticated','{sig}','EXECUTE') AS auth, "
            f"has_function_privilege('service_role','{sig}','EXECUTE') AS svc"
        )
        assert perms == {"anon": False, "auth": False, "svc": True}, sig
    internal_perm = pg.row(
        "SELECT has_function_privilege('anon','public.access_grant_agent_workspace(uuid,text)','EXECUTE') AS anon, "
        "has_function_privilege('authenticated','public.access_grant_agent_workspace(uuid,text)','EXECUTE') AS auth, "
        "has_function_privilege('service_role','public.access_grant_agent_workspace(uuid,text)','EXECUTE') AS svc"
    )
    assert internal_perm == {"anon": False, "auth": False, "svc": False}
