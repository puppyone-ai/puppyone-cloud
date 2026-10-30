"""Actual SSH, PTY, PostgreSQL/PostgREST and disposable Docker or opted-in Fly."""

import asyncio
import base64
import hashlib
import json
import os
import subprocess
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import httpx
import pytest
from fastapi import FastAPI

from src.platform.access.adapters.api.workbench_router import (
    get_workbench_service,
    workspace_error_handler,
)
from src.platform.access.adapters.api.workbench_router import router as files_router
from src.platform.access.adapters.ssh.gateway import start_gateway
from src.platform.access.adapters.ssh.hosted import (
    HostedSshAccess,
    SshGrantRepository,
)
from src.platform.access.adapters.ssh.hosted_router import get_hosted_ssh_service, router
from src.platform.auth.dependencies import get_current_user
from src.platform.workspace.connections import ConnectionRepository, WorkspaceConnections
from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.execution.workbench_host import WorkbenchChannel
from src.platform.workspace.filesystem.sandbox import SandboxFilesystem
from src.platform.workspace.repository import WorkbenchRepository
from src.platform.workspace.service import WorkbenchService
from tests.agent.runtime.test_live_workbench_control import default, rpc, transition

pytestmark = pytest.mark.integration
IMAGE = "puppyone-workspace-ssh-acceptance:local"
PROVIDER = os.environ.get("CLOUD_AGENT_TEST_SSH_PROVIDER", "docker")


@pytest.fixture
async def live(postgres, submitted, control_api, tmp_path, monkeypatch):
    args, _ = submitted
    if PROVIDER not in {"docker", "fly"}:
        pytest.fail("Unsupported acceptance provider")
    if PROVIDER == "fly" and os.environ.get("CLOUD_AGENT_TEST_FLY") != "1":
        pytest.fail("Fly acceptance requires explicit opt-in")
    from src.config import settings

    # Preserve bounded transport diagnostics for a failed live acceptance. No
    # protocol behavior is mocked; only the normally discarded CLI stderr is read.
    diagnostics = []
    if PROVIDER == "fly":
        from src.platform.sandbox.providers.fly import provider as fly

        read_process = fly.read_process

        def record_transport(host):
            read_process(host)
            host.stderr_reader.cancel()

            async def stderr():
                tail = b""
                while chunk := await host.process.stderr.read(4096):
                    tail = (tail + chunk)[-8192:]
                diagnostics.append(tail.decode(errors="replace"))

            host.stderr_reader = asyncio.create_task(stderr())

        monkeypatch.setattr(fly, "read_process", record_transport)

    artifact = settings.CLOUD_AGENT_FLY_IMAGE if PROVIDER == "fly" else IMAGE
    row = default(postgres, args)
    row = rpc(
        postgres,
        "acquire",
        workbench=row["id"],
        user=args["user"],
        project=args["project"],
        binding={"provider": PROVIDER, "artifact": artifact},
    )["workbench"]
    owner = WorkbenchChannel(row)
    workspace = None
    gateway = None
    sessions, clients = set(), set()
    try:
        # Persist allocation intent before I/O, including a lost create reply.
        row = transition(postgres, row, "allocating", owner.resource)
        resource = await owner.create()
        await owner.connect()
        await owner.control(
            "initialize",
            dict(
                project_id=args["project"],
                object_format="sha1",
                target_ref=row["target_ref"],
                base_oid=None,
                readonly=False,
            ),
        )
        row = transition(postgres, row, "running", resource)
        await owner.close()
        workspace = WorkbenchService(
            None,
            SandboxFilesystem(),
            SimpleNamespace(check_session=AsyncMock()),
            repository=WorkbenchRepository(control_api),
        )
        connections = WorkspaceConnections(workspace, ConnectionRepository(control_api))
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        client_key = asyncssh.generate_private_key("ssh-ed25519")
        config = SimpleNamespace(
            public_host="127.0.0.1",
            public_port=2222,
            public_host_key=host_key.export_public_key().decode(),
        )
        service = HostedSshAccess(workspace, SshGrantRepository(control_api), connections, config)
        gateway = await start_gateway(
            service,
            host_key,
            host="127.0.0.1",
            port=0,
            connections=clients,
            sessions=sessions,
        )
        config.public_port = gateway.get_port()
        key_file = tmp_path / "client-key"
        key_file.write_bytes(client_key.export_private_key())
        key_file.chmod(0o600)
        known = tmp_path / "known_hosts"
        known.write_text(service.endpoint()["known_hosts"] + "\n")
        app = FastAPI()
        app.include_router(router)
        app.include_router(files_router)
        app.add_exception_handler(WorkspaceError, workspace_error_handler)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=args["user"])
        app.dependency_overrides[get_hosted_ssh_service] = lambda: service
        app.dependency_overrides[get_workbench_service] = lambda: workspace
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fixture"
        ) as api:
            yield SimpleNamespace(
                args=args,
                row=row,
                owner=owner,
                workspace=workspace,
                service=service,
                key=client_key,
                key_file=key_file,
                known=known,
                port=config.public_port,
                api=api,
                db=postgres,
            )
    finally:
        if gateway:
            gateway.close()
            await gateway.wait_closed()
        for client in list(clients):
            client.close()
        await asyncio.gather(*list(sessions), return_exceptions=True)
        if workspace:
            await workspace.close()
        await owner.close()
        for diagnostic in diagnostics:
            print(diagnostic.replace(settings.CLOUD_AGENT_FLY_TOKEN, "[redacted]"))
        if PROVIDER == "fly":
            from tests.agent.workbench_acceptance.accept_fly import cleanup_owned

            case = SimpleNamespace(client=control_api, project=args["project"])
            await cleanup_owned(case, owner.runtime, {})
            await owner.runtime.aclose()
        else:
            with suppress(Exception):
                await owner.runtime._docker("rm", "-f", owner.resource["resource_id"])
            for volume in ("files", "control"):
                with suppress(Exception):
                    await owner.runtime._docker(
                        "volume", "rm", "puppyone-workbench-" + volume + "-" + row["generation"]
                    )


async def issue(live):
    response = await live.api.post(
        f"/api/v1/projects/{live.args['project']}/workbench/ssh-grants",
        json={
            "generation": live.row["generation"],
            "public_key": live.key.export_public_key().decode(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def connect(live, grant):
    return await asyncssh.connect(
        "127.0.0.1",
        port=live.port,
        username=grant["username"],
        client_keys=[live.key],
        known_hosts=str(live.known),
        encoding=None,
    )


async def read_file(live, path):
    response = await live.api.get(
        f"/api/v1/projects/{live.args['project']}/workbench/file",
        params={"generation": live.row["generation"], "path": path},
    )
    assert response.status_code == 200, response.text
    return base64.b64decode(response.json()["content_base64"])


async def until_released(live, identity):
    async with asyncio.timeout(25):
        while True:
            row = await asyncio.to_thread(
                live.db.row, f"SELECT * FROM cloud_workbench_connections WHERE id='{identity}'"
            )
            if row and row["state"] == "released":
                return row
            await asyncio.sleep(0.1)


async def test_stock_ssh_exec_binary_and_editor_same_uncommitted_files(live):
    grant = await issue(live)
    payload = b"\x00\xffhello native filesystem\n" * 16384
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={live.known}",
        "-i",
        str(live.key_file),
        "-p",
        str(live.port),
        f"{grant['username']}@127.0.0.1",
        "cat > native.bin; sha256sum native.bin; printf stderr-test >&2; exit 7",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(45):
        stdout, stderr = await proc.communicate(payload)
    assert proc.returncode == 7, (stdout, stderr)
    assert hashlib.sha256(payload).hexdigest().encode() in stdout
    assert b"stderr-test" in stderr
    assert await read_file(live, "native.bin") == payload
    assert (await until_released(live, grant["id"]))["resource"]["resource_id"] == live.row[
        "resource"
    ]["resource_id"]
    # The key remains valid, but the delegated connection is single-use.
    with pytest.raises(asyncssh.PermissionDenied):
        await connect(live, grant)


async def test_interactive_pty_preserves_shell_context_resize_and_editor_visibility(live):
    grant = await issue(live)
    events = live.workspace.events(live.args["user"], live.args["project"], live.row["generation"])
    assert (await anext(events))["reset"]

    async def changed():
        async for event in events:
            if event and "context.txt" in (event.get("paths") or []):
                return event

    live_events = asyncio.create_task(changed())
    try:
        async with await connect(live, grant) as conn:
            proc = await conn.create_process(term_type="xterm", term_size=(80, 24), encoding=None)
            proc.stdin.write(b"mkdir -p nested; cd nested; export CONTEXT=retained\n")
            proc.stdin.write(
                b"printf '%s' \"$CONTEXT\" > ../context.txt; printf '\\nREADY-PTY\\n'\n"
            )
            async with asyncio.timeout(20):
                await proc.stdout.readuntil(b"\r\nREADY-PTY\r\n")
            assert await read_file(live, "context.txt") == b"retained"
            async with asyncio.timeout(15):
                assert (await live_events)["reset"]
            proc.change_terminal_size(100, 40)
            proc.stdin.write(b"stty size; printf '\\nRESIZED-PTY\\n'\n")
            async with asyncio.timeout(10):
                data = await proc.stdout.readuntil(b"\r\nRESIZED-PTY\r\n")
            assert b"40 100" in data, data
            proc.stdin.write(b"exit 0\n")
            async with asyncio.timeout(20):
                await proc.wait_closed()
            assert proc.exit_status == 0
    finally:
        live_events.cancel()
        await asyncio.gather(live_events, return_exceptions=True)
        await events.aclose()
    await until_released(live, grant["id"])


async def test_disconnect_kills_descendants_without_losing_files(live):
    grant = await issue(live)
    conn = await connect(live, grant)
    proc = await conn.create_process(
        "sleep 300 </dev/null >/dev/null 2>&1 & echo $! > child.pid; echo connected; sleep 300",
        encoding=None,
    )
    async with asyncio.timeout(20):
        assert await proc.stdout.readline() == b"connected\n"
    pid = (await read_file(live, "child.pid")).decode().strip()
    conn.abort()
    await conn.wait_closed()
    await until_released(live, grant["id"])
    inspection = await issue(live)
    async with await connect(live, inspection) as verifier:
        result = await verifier.run(
            f'test ! -e /proc/{pid}/stat || test "$(cut -d " " -f 3 /proc/{pid}/stat)" = Z',
            encoding=None,
        )
        assert result.exit_status == 0
    assert await read_file(live, "child.pid") == f"{pid}\n".encode()


@pytest.mark.parametrize("reason", ["revoked", "expired"])
async def test_revoked_or_expired_grant_closes_session_and_denies_wrong_key(live, reason):
    grant = await issue(live)
    with pytest.raises(asyncssh.PermissionDenied):
        await asyncssh.connect(
            "127.0.0.1",
            port=live.port,
            username=grant["username"],
            client_keys=[asyncssh.generate_private_key("ssh-ed25519")],
            known_hosts=str(live.known),
        )
    async with await connect(live, grant) as conn:
        proc = await conn.create_process("echo connected; sleep 300", encoding=None)
        async with asyncio.timeout(20):
            assert await proc.stdout.readline() == b"connected\n"
        if reason == "revoked":
            response = await live.api.delete(
                f"/api/v1/projects/{live.args['project']}/workbench/ssh-grants/{grant['id']}"
            )
            assert response.status_code == 200, response.text
        else:
            # Advance only this local fixture's grant expiry, not the VM clock.
            live.db.sql(
                "UPDATE workspace_ssh_grants SET expires_at=created_at+interval '1 millisecond' "
                f"WHERE id='{grant['id']}'"
            )
        async with asyncio.timeout(25):
            await proc.wait_closed()
        # An expected ending explains itself and exits 1 (#1421); 255 is
        # reserved for unexpected failures.
        assert proc.exit_status == 1
        assert b"PuppyOne: " in await proc.stderr.read()
    await until_released(live, grant["id"])


async def test_new_owner_reaps_lost_gateway_connection(live):
    from uuid import uuid4

    from src.platform.workspace.execution.shell import WorkspaceShell

    identity = str(uuid4())
    connections = live.service.connections
    row, revision = await connections.authorize(
        live.args["user"], live.args["project"], live.row["id"], live.row["generation"]
    )
    record = await asyncio.to_thread(connections.repository.reserve, identity, row, revision)
    shell = WorkspaceShell(record)
    try:
        await shell.connect(await live.workspace.connection(row))
        await shell.control(
            "start",
            {
                "command": "sleep 300 </dev/null >/dev/null 2>&1 & echo $! > orphan.pid; echo connected; sleep 300",
                "term": "",
                "size": [80, 24],
            },
        )
        async with asyncio.timeout(20):
            while True:
                event = await shell.receive()
                if event["type"] == "shell_data" and b"connected" in base64.b64decode(
                    event["data"]
                ):
                    break
        pid = (await read_file(live, "orphan.pid")).decode().strip()
    finally:
        # Lose the transport without executing the normal gateway cleanup path.
        await shell.close()
    live.db.sql(
        "UPDATE cloud_workbench_connections SET lease_until=now()-interval '1 second' "
        f"WHERE id='{identity}'"
    )
    replacement = WorkspaceConnections(
        live.workspace, ConnectionRepository(connections.repository.client)
    )
    await replacement.reap()
    await until_released(live, identity)
    async with await connect(live, await issue(live)) as verifier:
        result = await verifier.run(
            f'test ! -e /proc/{pid}/stat || test "$(cut -d " " -f 3 /proc/{pid}/stat)" = Z',
            encoding=None,
        )
        assert result.exit_status == 0


@asynccontextmanager
async def agent_machines(live, transport=None):
    """A first connection starts a machine, so the sandbox module has to be running.

    Without a real ``transport`` the Project is empty: a Workspace initializes from no base.
    """
    workspace = live.workspace

    def describe(project, grant, target_ref=None):
        return {
            "object_format": "sha1",
            "target_ref": target_ref or "refs/heads/main",
            "expected_oid": None,
        }

    transport = transport or SimpleNamespace(describe=describe)
    workspace.transport = workspace.lifecycle.transport = transport
    meter = SimpleNamespace(
        ensure_session=AsyncMock(),
        check_session=AsyncMock(),
        detach_session=AsyncMock(),
        resume_session=AsyncMock(),
        finish_session=AsyncMock(return_value=True),
    )
    workspace.billing = workspace.lifecycle.billing = meter
    stop = asyncio.Event()
    lifecycle = asyncio.create_task(workspace.lifecycle.serve(stop=stop))
    try:
        yield
    finally:
        stop.set()
        await asyncio.wait_for(lifecycle, 15)
        machines = await asyncio.to_thread(
            live.db.sql,
            f"""SELECT coalesce(string_agg(resource->>'resource_id',' '),'') FROM cloud_workbenches
            WHERE project_id='{live.args['project']}' AND agent_registration_id IS NOT NULL""",
        )
        for machine in machines.split():
            await asyncio.to_thread(
                subprocess.run, ["docker", "rm", "-f", machine], capture_output=True
            )


async def test_registered_external_agent_real_shell_and_parent_revocation(live):
    """A registered Agent reaches its own machine, not the owner's, then loses its shell."""
    from src.platform.access.grant_repository import AccessRepository, GrantRepository
    from src.platform.access.grants import GrantService
    from src.platform.access.sessions import AccessSessions
    from tests.agent.runtime.test_access_lifecycle import command, setup_access
    from tests.agent.runtime.test_access_lifecycle import issue as issue_registered

    db_client = live.service.grants.client
    live.service.authority = GrantService(GrantRepository(db_client))
    live.service.sessions = AccessSessions(AccessRepository(db_client))
    context = await asyncio.to_thread(setup_access, live.db, live.args, workspace=live.row)
    # What the renewal endpoint does on first contact, before the client retries.
    machine = json.loads(
        await asyncio.to_thread(
            live.db.sql,
            f"SELECT to_jsonb(public.access_grant_agent_workspace('{context['grant']}'))",
        )
    )
    assert machine["id"] != live.row["id"]
    async with agent_machines(live):
        await asyncio.wait_for(
            live.workspace.ensure_running(
                {"user_id": machine["user_id"], "project_id": machine["project_id"]}, machine
            ),
            120,
        )
        ticket, _, _ = await asyncio.to_thread(
            issue_registered, live.db, context, channel="ssh", public_key=live.key.export_public_key().decode().strip()
        )
        grant = {**ticket, "username": ticket["id"]}
        assert grant["workbench_id"] == machine["id"]
        async with await connect(live, grant) as conn:
            proc = await conn.create_process("printf registered > registered.txt; echo ready; sleep 300", encoding="utf-8")
            async with asyncio.timeout(20):
                assert await proc.stdout.readline() == "ready\n"
            hidden = await live.api.get(
                f"/api/v1/projects/{live.args['project']}/workbench/file",
                params={"generation": live.row["generation"], "path": "registered.txt"},
            )
            assert hidden.status_code == 404
            await asyncio.to_thread(command, live.db, "grant", "grant_state", context["owner"],
                                    id=context["grant"], project_id=context["project"], state="revoked")
            async with asyncio.timeout(25):
                await proc.wait_closed()
        await until_released(live, ticket["id"])
        row = await asyncio.to_thread(live.db.row, f"SELECT * FROM access_sessions WHERE id='{ticket['id']}'")
        assert row["state"] == "closed" and row["registration_id"] == context["registration"]
        with pytest.raises(asyncssh.PermissionDenied):
            await connect(live, grant)


@pytest.mark.skipif(not os.environ.get("PUPPYONE_TEST_CLI_ROOT"), reason="standalone CLI candidate required")
async def test_cli_invitation_approval_and_stock_ssh(live, tmp_path, monkeypatch):
    """Real external CLI, HTTP, SQL, gateway and VM; only login/billing are fixture inputs."""
    import json
    import secrets
    import socket
    from pathlib import Path
    from uuid import uuid4

    import uvicorn
    from fastapi import Header, HTTPException

    from src.config import settings
    from src.platform.access import lifecycle_router as routes
    from src.platform.access.adapters.ssh import hosted
    from src.platform.access.credentials import CredentialService
    from src.platform.access.grant_repository import AccessRepository, GrantRepository
    from src.platform.access.grants import GrantService
    from src.platform.access.registration_repository import RegistrationRepository
    from src.platform.access.registrations import RegistrationService
    from src.platform.access.sessions import AccessSessions
    from src.platform.auth.models import CurrentUser

    cli = os.environ.get("PUPPYONE_TEST_CLI_ROOT")
    if not cli:
        pytest.skip("Set PUPPYONE_TEST_CLI_ROOT to the candidate standalone CLI checkout")
    script = Path(cli) / "tests/access_live_client.mjs"
    assert script.is_file()
    guest, owner_key, guest_key = str(uuid4()), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await asyncio.to_thread(live.db.sql, f"INSERT INTO auth.users(id) VALUES('{guest}');")
    client = live.service.grants.client
    authority = GrantService(GrantRepository(client))
    live.service.authority = authority
    live.service.sessions = AccessSessions(AccessRepository(client))
    monkeypatch.setattr(settings, "ACCESS_LIFECYCLE_ENABLED", True)
    monkeypatch.setattr(hosted, "ssh_settings", lambda: live.service.config)
    app = FastAPI()
    app.include_router(routes.router)

    def identity(authorization: str = Header(default="")):
        users = {"Bearer " + owner_key: live.args["user"], "Bearer " + guest_key: guest}
        if authorization not in users:
            raise HTTPException(401)
        return CurrentUser(user_id=users[authorization], role="authenticated", is_anonymous=False)

    app.dependency_overrides[get_current_user] = identity
    app.dependency_overrides[routes.registration_service] = lambda: RegistrationService(RegistrationRepository(client))
    app.dependency_overrides[routes.grant_service] = lambda: authority
    app.dependency_overrides[routes.credential_service] = lambda: CredentialService(AccessRepository(client))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
        result_file = tmp_path / "result.json"
        process = await asyncio.create_subprocess_exec(
            "node", str(script), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PUPPY_ACCESS_TEST_API": f"http://127.0.0.1:{port}",
                 "PUPPY_ACCESS_TEST_PROJECT": live.args["project"], "PUPPY_ACCESS_TEST_WORKSPACE": live.row["id"],
                 "PUPPY_ACCESS_TEST_OWNER": owner_key, "PUPPY_ACCESS_TEST_GUEST": guest_key,
                 "PUPPY_ACCESS_TEST_STORE": str(tmp_path / "client"), "PUPPY_ACCESS_TEST_RESULT": str(result_file)},
        )
        async with asyncio.timeout(90):
            stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode().replace(owner_key, "[redacted]").replace(guest_key, "[redacted]")
        assert b"cli-connected" in stdout
        assert await read_file(live, "cli-access.txt") == b"cli-through-http"
        result = json.loads(result_file.read_text())
        await until_released(live, result["ticket"])
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        server.should_exit = True
        await serving
        listener.close()
