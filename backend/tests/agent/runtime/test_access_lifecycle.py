"""Real PostgreSQL acceptance for durable authority and protocol-specific proofs."""

import hashlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.agent.runtime.conftest import ROOT, Database, literal
from tests.agent.runtime.test_live_workbench_control import running
from tests.agent.runtime.test_workspace_connections import reserve, update

pytestmark = pytest.mark.integration


def call(db, name, **values):
    args = ",".join("p_" + k + "=>" + literal(v) for k, v in values.items())
    value = db.sql(f"SELECT to_jsonb(public.{name}({args}));")
    return json.loads(value) if value else None


def command(db, boundary, action, user, **payload):
    return call(db, "access_" + boundary + "_command", action=action, user=user, payload=payload)


def setup_access(db, args, *, workspace=None, channels=None, single_session=False, wake=False):
    owner, project = args["user"], args["project"]
    guest, registration, invitation, request, surface = [str(uuid4()) for _ in range(5)]
    org = db.row(f"SELECT org_id FROM projects WHERE id='{project}'")["org_id"]
    db.sql(f"INSERT INTO auth.users(id) VALUES('{guest}');")
    initial = uuid4().hex * 2
    command(
        db,
        "registration",
        "create",
        guest,
        id=registration,
        name="External Agent",
        key_hash=initial,
    )
    caps = (
        ["workspace.read", "workspace.write", "workspace.shell"] + (["workspace.wake"] if wake else [])
        if workspace
        else ["repository.read", "repository.write"]
    )
    channels = channels or (["ssh"] if workspace else ["git", "cli", "mcp"])
    invitation_hash = uuid4().hex * 2
    command(
        db,
        "grant",
        "invite",
        owner,
        id=invitation,
        key_hash=invitation_hash,
        project_id=project,
        target_kind="workspace" if workspace else "project_root",
        workbench_id=workspace["id"] if workspace else None,
        capabilities=caps,
        channels=channels,
        expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    command(
        db,
        "grant",
        "request",
        guest,
        id=request,
        registration_id=registration,
        key_hash=invitation_hash,
    )
    grant = command(
        db,
        "grant",
        "decide",
        owner,
        id=request,
        project_id=project,
        decision="approve",
        single_session=single_session,
    )
    if not workspace:
        kind = "mcp" if channels == ["mcp"] else "git_remote"
        db.sql(
            f"INSERT INTO access_surfaces(id,project_id,org_id,kind,name,created_by) VALUES('{surface}','{project}','{org}','{kind}','External','{owner}');"
        )
    return dict(
        owner=owner,
        project=project,
        guest=guest,
        registration=registration,
        grant=grant["id"],
        secret=initial,
        surface=surface,
        invitation=invitation,
        request=request,
    )


def issue(db, context, *, channel="git", request=None, next_key=None, secret=None, public_key=None):
    request = request or str(uuid4())
    next_key = next_key or uuid4().hex * 2
    token = hashlib.sha256(request.encode()).hexdigest()
    key = secret or context["secret"]
    args = dict(next_key_hash=next_key, request=request, grant=context["grant"], channel=channel)
    if channel == "ssh":
        args["public_key"] = (
            public_key or "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOV/test"
        )  # SQL verifier is opaque; adapter validates encoding.
    else:
        args.update(credential_hash=token, surface=context["surface"])
    values = ",".join("p_" + k + "=>" + literal(v) for k, v in args.items())
    result = db.sql(
        f"SELECT access_issue_credential(p_key_hashes=>ARRAY[{literal(key)}],{values});"
    )
    return json.loads(result), next_key, token


def test_management_reads_are_owned_and_do_not_disclose_credentials(postgres, submitted):
    args, _ = submitted
    context = setup_access(postgres, args)
    requests = call(postgres, "access_inspect", user=context["guest"],
                    collection="requests", registration=context["registration"])
    assert requests[0]["grant_id"] == context["grant"]
    assert requests[0]["registration_name"] == "External Agent"
    assert requests[0]["capabilities"] == ["repository.read", "repository.write"]
    for collection in ["requests", "invitations", "grants", "sessions"]:
        rows = call(postgres, "access_inspect", user=context["owner"],
                    collection=collection, project=context["project"])
        assert "key_hash" not in json.dumps(rows)
        assert context["secret"] not in json.dumps(rows)
    with pytest.raises(RuntimeError, match="access_denied"):
        call(postgres, "access_inspect", user=context["owner"],
             collection="requests", registration=context["registration"])
    with pytest.raises(RuntimeError, match="access_denied"):
        call(postgres, "access_inspect", user=context["guest"],
             collection="grants", project=context["project"])
    with pytest.raises(RuntimeError, match="access_denied"):
        call(postgres, "access_inspect", user=context["guest"],
             collection="grants", registration=context["registration"])


@pytest.mark.parametrize("channel", ["git", "cli", "mcp"])
def test_durable_registration_protocol_credentials_and_current_revocation(
    postgres, submitted, channel
):
    args, _ = submitted
    context = setup_access(postgres, args, channels=[channel])
    receipt, _, token = issue(postgres, context, channel=channel)
    function = "access_managed_credential" if channel == "mcp" else "resolve_access_git_credential"
    accepted = call(postgres, function, key_hash=token, channel=channel)
    assert accepted and receipt["registration_id"] == context["registration"]
    if channel != "mcp":
        assert accepted["registration_id"] == context["registration"]
        assert accepted["effective_mode"] == "rw"
        assert (
            call(postgres, function, key_hash=token, channel="cli" if channel == "git" else "git")
            is None
        )
    command(
        postgres,
        "grant",
        "grant_state",
        context["owner"],
        id=context["grant"],
        project_id=context["project"],
        state="revoked",
    )
    assert call(postgres, function, key_hash=token, channel=channel) is None
    assert call(postgres, "resolve_access_git_credential", key_hash=token, channel="git") is None
    with pytest.raises(RuntimeError, match="invalid_transition"):
        command(
            postgres,
            "grant",
            "grant_state",
            context["owner"],
            id=context["grant"],
            project_id=context["project"],
            state="active",
        )


def test_rotation_retry_and_reuse_detection_require_owner_reset(postgres, submitted):
    context = setup_access(postgres, submitted[0])
    request = str(uuid4())
    receipt, next_key, token = issue(postgres, context, request=request)
    assert issue(postgres, context, request=request, next_key=next_key)[0] == receipt
    reused, _, _ = issue(postgres, context)
    assert reused["error"] == "access_credential_reused"
    assert (
        command(postgres, "registration", "get", context["guest"], id=context["registration"])[
            "state"
        ]
        == "suspended"
    )
    command(
        postgres,
        "registration",
        "state",
        context["guest"],
        id=context["registration"],
        state="active",
    )
    assert issue(postgres, context, secret=next_key)[0]["error"] == "access_invalid_credential"
    assert call(postgres, "resolve_access_git_credential", key_hash=token, channel="git") is None
    replacement = uuid4().hex * 2
    command(
        postgres,
        "registration",
        "reset_key",
        context["guest"],
        id=context["registration"],
        key_hash=replacement,
    )
    # Different protocol credential hash avoids the intentionally unique old hash.
    assert (
        call(postgres, "access_grant_context", grant=context["grant"], channel="git")[
            "registration"
        ]["id"]
        == context["registration"]
    )


def test_approver_loss_and_client_suspension_fail_closed(postgres, submitted):
    args = dict(submitted[0])
    admin = str(uuid4())
    org = postgres.row(f"SELECT org_id FROM projects WHERE id='{args['project']}'")["org_id"]
    postgres.sql(
        f"INSERT INTO auth.users(id) VALUES('{admin}'); INSERT INTO org_members(org_id,user_id,role) VALUES('{org}','{admin}','member'); INSERT INTO project_members(project_id,org_id,user_id,role) VALUES('{args['project']}','{org}','{admin}','admin');"
    )
    args["user"] = admin
    context = setup_access(postgres, args)
    _, _, token = issue(postgres, context)
    command(
        postgres,
        "registration",
        "state",
        context["guest"],
        id=context["registration"],
        state="suspended",
    )
    assert call(postgres, "resolve_access_git_credential", key_hash=token, channel="git") is None
    command(
        postgres,
        "registration",
        "state",
        context["guest"],
        id=context["registration"],
        state="active",
    )
    assert call(postgres, "resolve_access_git_credential", key_hash=token, channel="git")
    postgres.sql(f"DELETE FROM org_members WHERE user_id='{context['owner']}';")
    assert call(postgres, "resolve_access_git_credential", key_hash=token, channel="git") is None


def test_workspace_session_outlives_login_ticket_but_not_parent(postgres, submitted):
    args, _ = submitted
    workspace = running(postgres, args)
    context = setup_access(postgres, args, workspace=workspace)
    ticket, _, _ = issue(postgres, context, channel="ssh")
    call(postgres, "workspace_ssh_grant_consume", id=ticket["id"])
    connection = reserve(postgres, args, workspace, ticket["id"])
    postgres.sql(
        f"UPDATE workspace_ssh_grants SET created_at=now()-interval '10 minutes', expires_at=now()-interval '1 second' WHERE id='{ticket['id']}';"
    )
    assert update(postgres, connection, "renew")["state"] == "active"
    command(
        postgres,
        "grant",
        "grant_state",
        context["owner"],
        id=context["grant"],
        project_id=context["project"],
        state="revoked",
    )
    with pytest.raises(RuntimeError, match="access_denied"):
        update(postgres, connection, "renew")
    pending = call(postgres, "access_session_command", action="pending")
    assert any(row["id"] == ticket["id"] for row in pending)
    assert (
        postgres.sql(
            f"SELECT cloud_workbench_has_occupants('{workspace['id']}','{workspace['generation']}')"
        )
        == "t"
    )
    update(postgres, connection, "stop")
    update(postgres, connection, "release")
    assert (
        postgres.row(f"SELECT state FROM access_sessions WHERE id='{ticket['id']}'")["state"]
        == "closed"
    )


def test_single_session_parallel_admission_only_one_wins(postgres, submitted):
    args, _ = submitted
    context = setup_access(postgres, args, workspace=running(postgres, args), single_session=True)
    ticket, _, _ = issue(postgres, context, channel="ssh")

    def consume(_):
        try:
            return call(postgres, "workspace_ssh_grant_consume", id=ticket["id"])
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consume, range(2)))
    assert sum(bool(result) for result in results) == 1
    call(postgres, "access_session_command", action="finish_unallocated", id=ticket["id"])
    with pytest.raises(RuntimeError, match="access_denied"):
        call(postgres, "access_grant_context", grant=context["grant"], channel="ssh")


def test_caller_cannot_manage_other_registration_or_approve_own_request(postgres, submitted):
    context = setup_access(postgres, submitted[0])
    with pytest.raises(RuntimeError, match="access_denied"):
        command(
            postgres,
            "registration",
            "state",
            context["owner"],
            id=context["registration"],
            state="revoked",
        )
    with pytest.raises(RuntimeError, match="access_denied"):
        command(
            postgres,
            "grant",
            "grant_state",
            context["guest"],
            id=context["grant"],
            project_id=context["project"],
            state="revoked",
        )
    for role in ("anon", "authenticated"):
        assert postgres.sql(f"SELECT has_table_privilege('{role}','access_grants','SELECT')") == "f"
        assert (
            postgres.sql(
                f"SELECT has_function_privilege('{role}','access_grant_context(uuid,text,uuid)','EXECUTE')"
            )
            == "f"
        )
    assert (
        postgres.sql("SELECT has_table_privilege('service_role','access_grants','UPDATE')") == "f"
    )


def test_populated_upgrade_preserves_existing_credentials_and_connections(postgres, submitted):
    spec = importlib.util.spec_from_file_location(
        "access_upgrade", ROOT / "scripts/testing/native_postgres.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = "access_upgrade_" + uuid4().hex
    postgres.sql(f"CREATE DATABASE {database}")
    scratch = Database(postgres.url.rsplit("/", 1)[0] + "/" + database)
    try:
        scratch.sql((ROOT / "scripts/testing/postgres_auth_stub.sql").read_text())
        migrations = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        for path in migrations:
            if path.name < "20261025110000":
                scratch.sql(module.product_migration_sql(path))
        args, _ = submitted
        user, project = args["user"], args["project"]
        org = postgres.row(f"SELECT org_id FROM projects WHERE id='{project}'")["org_id"]
        surface, credential, ticket = [str(uuid4()) for _ in range(3)]
        scratch.sql(f"""
          BEGIN;
          INSERT INTO auth.users(id) VALUES('{user}');
          INSERT INTO organizations(id,name,slug,created_by) VALUES('{org}','Preserved','access-{org}','{user}');
          INSERT INTO org_members(org_id,user_id,role) VALUES('{org}','{user}','owner');
          INSERT INTO projects(id,name,org_id,created_by,lifecycle_status) VALUES('{project}','Preserved','{org}','{user}','ready');
          INSERT INTO project_members(project_id,org_id,user_id,role) VALUES('{project}','{org}','{user}','admin');
          INSERT INTO access_surfaces(id,project_id,org_id,kind,name,created_by) VALUES('{surface}','{project}','{org}','git_remote','Preserved','{user}');
          INSERT INTO access_surface_credentials(id,org_id,project_id,access_surface_id,credential_type,key_prefix,key_last4,key_hash,created_by)
          VALUES('{credential}','{org}','{project}','{surface}','git_http_token','git','1234','{"a" * 64}','{user}');
          COMMIT;
        """)
        workspace = running(scratch, args)
        connection = reserve(scratch, args, workspace)
        scratch.sql(
            f"INSERT INTO workspace_ssh_grants(id,workbench_id,generation,user_id,project_id,public_key,expires_at) VALUES('{ticket}','{workspace['id']}','{workspace['generation']}','{user}','{project}','ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOVfixturefixturefixture',now()+interval '10 minutes');"
        )
        before = {
            table: scratch.row(f"SELECT * FROM {table} WHERE id='{identity}'")
            for table, identity in (
                ("projects", project),
                ("cloud_workbenches", workspace["id"]),
                ("cloud_workbench_connections", connection["id"]),
                ("access_surface_credentials", credential),
                ("workspace_ssh_grants", ticket),
            )
        }
        candidates = [p for p in migrations if p.name >= "20261025110000"]
        # Every additive migration is transactional; roll back the whole candidate chain.
        combined = "\n".join(
            p.read_text().replace("BEGIN;", "").replace("COMMIT;", "") for p in candidates
        )
        scratch.sql("BEGIN;\n" + combined + "\nROLLBACK;")
        assert scratch.sql("SELECT to_regclass('public.access_grants') IS NULL") == "t"
        for path in candidates:
            scratch.sql(path.read_text())
        for table, row in before.items():
            after = scratch.row(f"SELECT * FROM {table} WHERE id='{row['id']}'")
            assert {key: after[key] for key in row} == row
        assert scratch.sql("SELECT count(*) FROM access_grants") == "0"
        resolved = call(scratch, "resolve_access_git_credential", key_hash="a" * 64, channel="git")
        assert resolved["credential_id"] == credential and resolved["registration_id"] is None
        assert call(scratch, "workspace_ssh_grant_consume", id=ticket)["used_at"]
        assert update(scratch, connection, "renew")["state"] == "active"
        update(scratch, connection, "stop")
        update(scratch, connection, "release")
    finally:
        postgres.sql(f"DROP DATABASE {database} WITH (FORCE)")


async def test_real_http_registration_approval_and_renewal(
    postgres, submitted, control_api, monkeypatch
):
    import httpx
    from fastapi import FastAPI

    from src.config import settings
    from src.platform.access import lifecycle_router as api
    from src.platform.access.credential_repository import (
        AccessCredentialRepository,
        access_token_hash,
        generate_access_token,
    )
    from src.platform.access.credentials import CredentialService
    from src.platform.access.grant_repository import AccessRepository, GrantRepository
    from src.platform.access.grants import GrantService
    from src.platform.access.registration_repository import RegistrationRepository
    from src.platform.access.registrations import RegistrationService
    from src.platform.auth.dependencies import get_current_user
    from src.platform.auth.models import CurrentUser

    monkeypatch.setattr(
        settings, "ACCESS_CREDENTIAL_HASH_SECRET", "owned-access-lifecycle-fixture-strong-secret"
    )
    monkeypatch.setattr(settings, "ACCESS_LIFECYCLE_ENABLED", True)
    args, _ = submitted
    project, owner = args["project"], args["user"]
    guest, reg_id, invitation_id, request_id, surface = [str(uuid4()) for _ in range(5)]
    org = postgres.row(f"SELECT org_id FROM projects WHERE id='{project}'")["org_id"]
    postgres.sql(
        f"INSERT INTO auth.users(id) VALUES('{guest}'); INSERT INTO access_surfaces(id,project_id,org_id,kind,name,created_by) VALUES('{surface}','{project}','{org}','git_remote','Registered','{owner}');"
    )
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.registration_service] = lambda: RegistrationService(
        RegistrationRepository(control_api)
    )
    app.dependency_overrides[api.grant_service] = lambda: GrantService(GrantRepository(control_api))
    app.dependency_overrides[api.credential_service] = lambda: CredentialService(
        AccessRepository(control_api)
    )
    actor = guest
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=actor, role="authenticated"
    )
    registration_secret, invitation_secret = (
        generate_access_token("par"),
        generate_access_token("pai"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://owned"
    ) as client:
        response = await client.post(
            "/api/v1/access/registrations",
            json=dict(id=reg_id, name="Agent B", secret=registration_secret),
        )
        assert response.status_code == 200, response.text
        assert registration_secret not in response.text and "key_hash" not in response.text
        actor = owner
        response = await client.post(
            f"/api/v1/projects/{project}/access/invitations",
            json=dict(
                id=invitation_id,
                secret=invitation_secret,
                target_kind="project_root",
                capabilities=["repository.read"],
                channels=["git"],
                expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            ),
        )
        assert response.status_code == 200, response.text
        actor = guest
        response = await client.post(
            "/api/v1/access/requests",
            json=dict(id=request_id, registration_id=reg_id, invitation=invitation_secret),
        )
        assert response.status_code == 200, response.text
        actor = owner
        response = await client.post(
            f"/api/v1/projects/{project}/access/requests/{request_id}/decision",
            json={"decision": "approve"},
        )
        assert response.status_code == 200, response.text
        grant = response.json()
        next_secret, token = generate_access_token("par"), generate_access_token("git")
        payload = dict(
            request_id=str(uuid4()),
            grant_id=grant["id"],
            channel="git",
            next_secret=next_secret,
            credential_secret=token,
            surface_id=surface,
        )
        response = await client.post(
            "/api/v1/access/credentials/renew",
            headers={"Authorization": "Bearer " + registration_secret},
            json=payload,
        )
        assert response.status_code == 200, response.text
        assert token not in response.text and next_secret not in response.text
        credential = AccessCredentialRepository(control_api).resolve_git_runtime_credential(token)
        assert credential["effective_mode"] == "r"
        assert credential["registration_id"] == reg_id
        for collection in ("invitations", "requests", "grants", "sessions"):
            response = await client.get(f"/api/v1/projects/{project}/access/{collection}")
            assert response.status_code == 200, response.text
        actor = guest
        response = await client.get(f"/api/v1/access/registrations/{reg_id}/grants")
        assert response.status_code == 200 and response.json()[0]["id"] == grant["id"]
        response = await client.post(
            "/api/v1/access/registrations",
            json=dict(id=str(uuid4()), name="Bad", secret="sensitive-bad-input"),
        )
        assert response.status_code == 422 and "sensitive-bad-input" not in response.text
        assert postgres.sql(
            f"SELECT key_hash FROM access_registration_credentials WHERE registration_id='{reg_id}' AND state='active'"
        ) == access_token_hash(next_secret)


def test_expiry_resource_and_capability_boundaries(postgres, submitted):
    args, _ = submitted
    context = setup_access(postgres, args, channels=["git"])
    with pytest.raises(RuntimeError, match="access_denied"):
        issue(postgres, context, channel="ssh")
    postgres.sql(
        f"UPDATE access_grants SET starts_at=now()-interval '1 day',expires_at=now()-interval '1 second' WHERE id='{context['grant']}';"
    )
    with pytest.raises(RuntimeError, match="access_denied"):
        issue(postgres, context)
    assert (
        postgres.sql(
            f"SELECT count(*) FROM access_surface_credentials WHERE access_grant_id='{context['grant']}'"
        )
        == "0"
    )


def test_session_disconnect_allows_reconnect_without_another_approval(postgres, submitted):
    args, _ = submitted
    workspace = running(postgres, args)
    context = setup_access(postgres, args, workspace=workspace)
    ticket, next_key, _ = issue(postgres, context, channel="ssh")
    call(postgres, "workspace_ssh_grant_consume", id=ticket["id"])
    connection = reserve(postgres, args, workspace, ticket["id"])
    command(
        postgres,
        "grant",
        "close_session",
        context["owner"],
        id=ticket["id"],
        project_id=context["project"],
    )
    assert any(
        row["id"] == ticket["id"]
        for row in call(postgres, "access_session_command", action="pending")
    )
    update(postgres, connection, "stop")
    update(postgres, connection, "release")
    new_ticket, _, _ = issue(postgres, context, channel="ssh", secret=next_key)
    assert new_ticket["id"] != ticket["id"]
    call(postgres, "workspace_ssh_grant_consume", id=new_ticket["id"])
    call(postgres, "access_session_command", action="finish_unallocated", id=new_ticket["id"])


def test_deleting_registration_cannot_turn_live_connection_into_legacy(postgres, submitted):
    args, _ = submitted
    workspace = running(postgres, args)
    context = setup_access(postgres, args, workspace=workspace)
    ticket, _, _ = issue(postgres, context, channel="ssh")
    call(postgres, "workspace_ssh_grant_consume", id=ticket["id"])
    connection = reserve(postgres, args, workspace, ticket["id"])
    postgres.sql(f"DELETE FROM access_registrations WHERE id='{context['registration']}';")
    with pytest.raises(RuntimeError, match="workspace_connection_expired"):
        update(postgres, connection, "renew")
    update(postgres, connection, "stop")
    update(postgres, connection, "release")


def test_rotation_concurrency_returns_one_credential_and_one_successor(postgres, submitted):
    context = setup_access(postgres, submitted[0])
    request, next_key = str(uuid4()), uuid4().hex * 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: issue(postgres, context, request=request, next_key=next_key)[0], range(2)
            )
        )
    assert results[0] == results[1]
    assert (
        postgres.sql(
            f"SELECT count(*) FROM access_registration_credentials WHERE registration_id='{context['registration']}' AND state='active'"
        )
        == "1"
    )
    assert (
        postgres.sql(
            f"SELECT count(*) FROM access_surface_credentials WHERE access_grant_id='{context['grant']}'"
        )
        == "1"
    )


def test_publication_authority_serializes_with_grant_revocation(postgres, submitted):
    """The existing final-publication resolver holds the parent through commit."""
    import time
    from threading import Event

    context = setup_access(postgres, submitted[0])
    receipt, _, _ = issue(postgres, context)
    lock_started = Event()
    marker = "access-fence-" + uuid4().hex

    def checked_operation():
        lock_started.set()
        return postgres.sql(
            f"SET application_name='{marker}'; BEGIN; SELECT access_credential_grant_valid('{receipt['id']}','git',true); SELECT pg_sleep(2.0); COMMIT;"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        checked = pool.submit(checked_operation)
        assert lock_started.wait(2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            # Lock acquisition, rather than a scheduling sleep, is the handoff.
            active = postgres.sql(
                f"SELECT count(*) FROM pg_stat_activity WHERE application_name='{marker}' AND wait_event='PgSleep'"
            )
            if active != "0":
                break
            time.sleep(0.01)
        else:
            pytest.fail("Checked operation never acquired its authority locks")
        revoke = pool.submit(
            command,
            postgres,
            "grant",
            "grant_state",
            context["owner"],
            id=context["grant"],
            project_id=context["project"],
            state="revoked",
        )
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            blocked = postgres.sql(
                f"SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%{context['grant']}%'"
            )
            if blocked != "0":
                break
            time.sleep(0.01)
        else:
            pytest.fail("Revocation did not wait for the checked operation's commit")
        assert checked.result(timeout=5).startswith("t")
        assert revoke.result(timeout=5)["state"] == "revoked"
    assert (
        call(postgres, "access_credential_grant_valid", id=receipt["id"], channel="git", write=True)
        is False
    )


def test_shared_policy_evaluates_real_workspace_authority(postgres, submitted):
    from src.exceptions import PermissionException
    from src.platform.authorization.service import AuthorizationService

    args, _ = submitted
    workspace = running(postgres, args)
    context = setup_access(postgres, args, workspace=workspace)
    facts = call(postgres, "access_grant_context", grant=context["grant"], channel="ssh")
    authority = AuthorizationService.authorize_delegated(
        facts, "ssh", project=context["project"], workbench=workspace["id"]
    )
    assert authority.subject_user_id == context["guest"]
    assert authority.approved_by == context["owner"]
    assert authority.registration_id == context["registration"]
    with pytest.raises(PermissionException):
        AuthorizationService.authorize_delegated(facts, "ssh", workbench=str(uuid4()))
    with pytest.raises(PermissionException):
        AuthorizationService.authorize_delegated(facts, "git")


@pytest.mark.parametrize("wake", [False, True])
def test_paused_workspace_credential_requires_the_wake_capability(postgres, submitted, wake):
    args, _ = submitted
    workspace = running(postgres, args)
    context = setup_access(postgres, args, workspace=workspace, wake=wake)
    postgres.sql(f"UPDATE cloud_workbenches SET state='paused' WHERE id='{workspace['id']}'")
    if not wake:
        with pytest.raises(RuntimeError, match="access_workspace_unavailable"):
            issue(postgres, context, channel="ssh")
        return
    ticket, next_key, _ = issue(postgres, context, channel="ssh")
    # Resuming keeps the generation, so the ticket stays bound to this workspace.
    assert ticket["generation"] == workspace["generation"]
    # A retired workspace is replaced on reopen; wake cannot carry a ticket across.
    postgres.sql(f"UPDATE cloud_workbenches SET state='retired' WHERE id='{workspace['id']}'")
    with pytest.raises(RuntimeError, match="access_workspace_unavailable"):
        issue(postgres, context, channel="ssh", secret=next_key)


def test_wake_is_only_a_workspace_capability(postgres, submitted):
    assert (
        postgres.sql(
            "SELECT public.access_authority_shape('project_root',"
            "ARRAY['repository.read','workspace.wake'],ARRAY['git'])"
        )
        == "f"
    )
    assert (
        postgres.sql(
            "SELECT public.access_authority_shape('workspace',"
            "ARRAY['workspace.read','workspace.write','workspace.shell','workspace.wake'],ARRAY['ssh'])"
        )
        == "t"
    )
