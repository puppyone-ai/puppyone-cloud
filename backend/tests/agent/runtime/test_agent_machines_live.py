"""External Agent machines: coming back to them, and what they cannot reach.

Real machines, real SSH and the real Git host. Docker runs the lifecycle case in
CI. The network boundary only means something on a VM provider, so that case
needs ``CLOUD_AGENT_TEST_SSH_PROVIDER=fly``.
"""

import asyncio
import json
import os
import shlex

import pytest

from src.config import settings
from src.platform.access.grant_repository import AccessRepository, GrantRepository
from src.platform.access.grants import GrantService
from src.platform.access.sessions import AccessSessions
from src.platform.sandbox.errors import WorkspaceUnavailable
from src.platform.sandbox.providers.fly.naming import workspace_app
from src.platform.sandbox.providers.fly.ssh import FlySSHConnections
from src.platform.sandbox.providers.fly.transport import acquire_transport
from tests.agent.runtime.test_access_lifecycle import issue as issue_registered
from tests.agent.runtime.test_access_lifecycle import setup_access
from tests.agent.runtime.test_hosted_ssh_live import (  # noqa: F401
    PROVIDER,
    agent_machines,
    connect,
    issue,
    live,
    until_released,
)
from tests.agent.runtime.test_supervisor import native_write
from tests.agent.runtime.test_supervisor import prepared as prepared_fixture

prepared = prepared_fixture
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def authorities(live):  # noqa: F811
    client = live.service.grants.client
    live.service.authority = GrantService(GrantRepository(client))
    live.service.sessions = AccessSessions(AccessRepository(client))


async def enroll(live):  # noqa: F811
    """One approved Agent and the machine its grant now points at."""
    context = await asyncio.to_thread(setup_access, live.db, live.args, workspace=live.row)
    machine = json.loads(
        await asyncio.to_thread(
            live.db.sql,
            f"SELECT to_jsonb(public.access_grant_agent_workspace('{context['grant']}'))",
        )
    )
    return context, machine


async def start(live, row):  # noqa: F811
    return await asyncio.wait_for(
        live.workspace.ensure_running(
            {"user_id": row["user_id"], "project_id": row["project_id"]}, row
        ),
        300,
    )


async def shell(live, context, command):  # noqa: F811
    """One Agent session; each renewal rotates the Agent's key, as the CLI does."""
    ticket, next_key, _ = await asyncio.to_thread(
        issue_registered,
        live.db,
        context,
        channel="ssh",
        public_key=live.key.export_public_key().decode().strip(),
    )
    context["secret"] = next_key
    async with await connect(live, {**ticket, "username": ticket["id"]}) as conn:
        result = await conn.run(command, check=False)
    await until_released(live, ticket["id"])
    return result


async def until_state(live, identity, state, timeout=240):  # noqa: F811
    async with asyncio.timeout(timeout):
        while True:
            row = await asyncio.to_thread(live.workspace.repository.get, identity)
            if row["state"] == state:
                return row
            await asyncio.sleep(1)


async def until_app_absent(live, resource):  # noqa: F811
    from tests.agent.workbench_acceptance.accept_fly import app_absent

    async with asyncio.timeout(60):
        while not await app_absent(live.owner.runtime, resource):
            await asyncio.sleep(1)


async def put_to_sleep(live, identity):  # noqa: F811
    """What maintenance does to an idle machine; its own loop may get there first."""
    await asyncio.to_thread(
        live.db.sql,
        f"UPDATE cloud_workbenches SET last_activity_at=now()-interval '1 day' WHERE id='{identity}'",
    )
    repository = live.workspace.repository
    row = await asyncio.to_thread(repository.get, identity)
    claimed = await asyncio.to_thread(repository.idle, row)
    if claimed["acquired"]:
        await live.workspace.lifecycle.pause(claimed["workbench"])
    return await until_state(live, identity, "paused")


@pytest.mark.parametrize("prepared", ["native"], indirect=True)
async def test_an_agent_comes_back_to_its_work_after_sleep_and_after_reclaim(prepared, live):  # noqa: F811
    case = prepared
    await native_write(case, "seed.md", b"seed")
    authorities(live)
    context, machine = await enroll(live)
    async with agent_machines(live, case.publication.transport):
        first = await start(live, machine)
        wrote = await shell(live, context, "printf 'kept\\n' > agent.md")
        assert wrote.exit_status == 0, wrote.stderr

        slept = await put_to_sleep(live, machine["id"])
        assert slept["unpublished_changes"] is False

        woken = await start(live, slept)
        assert woken["generation"] == first["generation"]
        assert woken["resource"]["resource_id"] == first["resource"]["resource_id"]
        read = await shell(live, context, "cat agent.md")
        assert read.stdout == b"kept\n", read.stderr

        retired = await reclaim(live, machine["id"])
        if PROVIDER == "fly" and settings.CLOUD_AGENT_FLY_ORG:
            await until_app_absent(live, first["resource"])
        elif PROVIDER == "fly":
            machines = await live.owner.runtime._request(first["resource"], "GET", "/machines")
            assert first["resource"]["resource_id"] not in {item["id"] for item in machines}

        rebuilt = await start(live, retired)
        assert rebuilt["id"] == machine["id"]
        assert rebuilt["generation"] != first["generation"]
        assert rebuilt["resource"]["resource_id"] != first["resource"]["resource_id"]
        assert rebuilt["target_ref"] == first["target_ref"]
        assert rebuilt["resource"].get("app") == first["resource"].get("app")
        read = await shell(live, context, "cat agent.md seed.md")
        assert read.stdout == b"kept\nseed", read.stderr


async def reclaim(live, identity):  # noqa: F811
    """Sleep past retention, then let maintenance destroy a machine whose work is saved.

    One sweep takes the 20 oldest rows, so this row goes first of those left by other tests.
    """
    await put_to_sleep(live, identity)
    await asyncio.to_thread(
        live.db.sql,
        f"""UPDATE cloud_workbenches SET paused_at=now()-interval '8 days',
        maintenance_after=now()-interval '10 years' WHERE id='{identity}'""",
    )
    await live.workspace.lifecycle.reap()
    return await until_state(live, identity, "retired")


@pytest.mark.skipif(not settings.CLOUD_AGENT_FLY_ORG, reason="needs per-Workspace apps")
@pytest.mark.parametrize("prepared", ["native"], indirect=True)
async def test_a_machine_on_the_shared_app_moves_to_its_own_network_when_rebuilt(
    prepared,
    live,  # noqa: F811
    monkeypatch,
):
    case = prepared
    await native_write(case, "seed.md", b"seed")
    authorities(live)
    context, machine = await enroll(live)
    async with agent_machines(live, case.publication.transport):
        with monkeypatch.context() as shared:
            shared.setattr(settings, "CLOUD_AGENT_FLY_ORG", "")
            first = await start(live, machine)
        assert first["resource"]["app"] == settings.CLOUD_AGENT_FLY_APP
        wrote = await shell(live, context, "printf 'kept\\n' > agent.md")
        assert wrote.exit_status == 0, wrote.stderr

        retired = await reclaim(live, machine["id"])
        rebuilt = await start(live, retired)
        assert rebuilt["resource"]["app"] == workspace_app(machine["id"])
        read = await shell(live, context, "cat agent.md seed.md")
        assert read.stdout == b"kept\nseed", read.stderr


REACH = """
import errno, json, os, socket, subprocess, sys
targets = json.loads(sys.argv[1])
seen = {"uid": os.getuid()}
for name, family, host, port in targets:
    with socket.socket(family, socket.SOCK_STREAM) as connection:
        connection.settimeout(4)
        try:
            connection.connect((host, port))
            seen[name] = "OPEN"
        except OSError as error:
            seen[name] = errno.errorcode.get(error.errno, "timeout")
try:
    seen["internal_dns"] = socket.getaddrinfo(sys.argv[2], 22)[0][4][0]
except OSError as error:
    seen["internal_dns"] = "unresolved"
run = lambda *command: subprocess.run(command, capture_output=True, text=True)
seen["nft_flush"] = run("sh", "-c", "nft flush ruleset").returncode
seen["sudo"] = run("sh", "-c", "sudo -n true").returncode
print(json.dumps(seen))
"""

ROOT_PUBLIC = """python3 -c 'import socket
socket.create_connection(("1.1.1.1",443),6);print("OPEN")'"""

PUBLIC_NAME = """python3 -c 'import socket
socket.create_connection(("example.com",443),8);print("OPEN")'"""

ROOT_REACH = """python3 -c 'import socket,sys
c=socket.socket(socket.AF_INET6);c.settimeout(6);c.connect((sys.argv[1],22));print("OPEN",c.recv(16))' """


async def as_root(resource, command):
    process = await asyncio.create_subprocess_exec(
        settings.CLOUD_AGENT_FLY_CLI,
        "ssh",
        "console",
        "--quiet",
        "--app",
        resource["app"],
        "--machine",
        resource["resource_id"],
        "--user",
        "root",
        "--command",
        command,
        env={**os.environ, "FLY_API_TOKEN": settings.CLOUD_AGENT_FLY_TOKEN},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(90):
        stdout, _ = await process.communicate()
    return stdout.decode(errors="replace")


async def same_network_machine(provider, row):
    """A control target that proves ``ROOT_REACH`` can succeed."""
    resource = row["resource"]
    created = await provider._request(
        resource,
        "POST",
        "/machines",
        json={
            "region": settings.CLOUD_AGENT_FLY_REGION,
            "config": {
                "image": resource["image_id"],
                "guest": {"cpu_kind": "shared", "cpus": 1, "memory_mb": 256},
                "init": {"exec": ["sleep", "infinity"]},
                "restart": {"policy": "no"},
            },
        },
    )
    await provider._request(
        resource,
        "GET",
        f"/machines/{created['id']}/wait",
        params={"state": "started", "timeout": 60},
    )
    return created["private_ip"]


async def reach_until_open(resource, address):
    answer = ""
    for _ in range(6):
        answer = await as_root(resource, ROOT_REACH + address)
        if "OPEN" in answer:
            break
        await asyncio.sleep(5)
    return answer


@pytest.mark.skipif(
    not os.environ.get("CLOUD_AGENT_TEST_FLY_CONNECTION_TOKEN"),
    reason="needs the connection token that api and ssh_gateway hold",
)
async def test_the_connection_token_reaches_a_machine_but_cannot_change_it(live):  # noqa: F811
    token = os.environ["CLOUD_AGENT_TEST_FLY_CONNECTION_TOKEN"]
    provider, resource = live.owner.runtime, live.row["resource"]
    path = f"/machines/{resource['resource_id']}"
    row = await provider._request(resource, "GET", path, token=token)

    transport = await acquire_transport(
        settings.CLOUD_AGENT_FLY_APP, token, settings.CLOUD_AGENT_FLY_CLI
    )
    pool = FlySSHConnections(
        settings.CLOUD_AGENT_FLY_APP, token, transport.cli, transport.env, idle_seconds=1
    )
    try:
        lease = await pool.open(resource, row["private_ip"], "printf connected")
        try:
            assert await lease.process.stdout.read() == b"connected"
        finally:
            lease.release()
    finally:
        await pool.close()
        transport.release()

    for method, suffix, body in (
        ("POST", "/machines", {"config": {"image": resource["image_id"]}}),
        ("POST", path + "/stop", {}),
        ("DELETE", path, None),
        ("POST", "/volumes", {"name": "nope", "region": "sin", "size_gb": 1}),
    ):
        with pytest.raises(WorkspaceUnavailable):
            await provider._request(resource, method, suffix, token=token, json=body)


@pytest.mark.skipif(PROVIDER != "fly", reason="the boundary under test is a VM provider's network")
async def test_an_agent_cannot_reach_another_machine_or_the_internet(live):  # noqa: F811
    from tests.agent.workbench_acceptance.accept_fly import firewall_hits

    authorities(live)
    first, first_machine = await enroll(live)
    second, second_machine = await enroll(live)
    async with agent_machines(live):
        mine = await start(live, first_machine)
        theirs = await start(live, second_machine)
        owner = await asyncio.to_thread(live.workspace.repository.get, live.row["id"])
        identities = {row["resource"]["resource_id"] for row in (mine, theirs, owner)}
        assert len(identities) == 3, "each trust principal has its own machine"

        provider = live.owner.runtime

        async def private_address(row):
            detail = await provider._request(
                row["resource"], "GET", f"/machines/{row['resource']['resource_id']}"
            )
            return detail["private_ip"]

        peer, internal = await private_address(theirs), await private_address(owner)

        if settings.CLOUD_AGENT_FLY_ORG:
            # Root owns the firewall, so only the private network can stop it here.
            blocked = await as_root(mine["resource"], ROOT_REACH + peer)
            assert "OPEN" not in blocked, blocked
            neighbor = await same_network_machine(provider, mine)
            assert "OPEN" in await reach_until_open(mine["resource"], neighbor)
        else:
            # The route exists, so the only thing stopping the Agent is the rule below.
            control = await as_root(mine["resource"], ROOT_REACH + peer)
            assert "OPEN" in control, control

        before = await firewall_hits(mine["resource"])
        targets = [
            ("other_agent", 10, peer, 22),
            ("internal_machine", 10, internal, 22),
            ("fly_private_gateway", 10, "fdaa::3", 53),
            ("public_v4", 2, "1.1.1.1", 443),
            ("public_v6", 10, "2606:4700:4700::1111", 443),
        ]
        script = shlex.quote(REACH)
        arguments = shlex.quote(json.dumps(targets))
        name = f"{theirs['resource']['resource_id']}.vm.{theirs['resource']['app']}.internal"
        reached = await shell(live, first, f"python3 -c {script} {arguments} {name}")
        assert reached.exit_status == 0, reached.stderr
        seen = json.loads(reached.stdout)
        print("AGENT_NETWORK_BOUNDARY " + json.dumps(seen), flush=True)
        assert seen["uid"] == 1000
        assert seen["nft_flush"] != 0
        assert seen["sudo"] != 0
        for name, *_ in targets:
            assert seen[name] != "OPEN", (name, seen)
        assert await firewall_hits(mine["resource"]) >= before + len(targets)
        # Root leaves the machine, so the rule is what stopped the Agent.
        assert "OPEN" in await as_root(mine["resource"], ROOT_PUBLIC)

        marked = await shell(live, second, "printf theirs > only-theirs.txt")
        assert marked.exit_status == 0, marked.stderr
        looked = await shell(live, first, "test ! -e only-theirs.txt")
        assert looked.exit_status == 0


@pytest.mark.skipif(PROVIDER != "fly", reason="the boundary under test is a VM provider's network")
@pytest.mark.skipif(not settings.CLOUD_AGENT_FLY_ORG, reason="needs per-Workspace apps")
async def test_the_internal_workspace_reaches_the_internet_but_no_private_address(live):  # noqa: F811
    from tests.agent.workbench_acceptance.accept_fly import firewall_hits

    authorities(live)
    _, machine = await enroll(live)
    async with agent_machines(live):
        agent = await start(live, machine)
        detail = await live.owner.runtime._request(
            agent["resource"], "GET", f"/machines/{agent['resource']['resource_id']}"
        )
        private = [
            ("agent_machine", 10, detail["private_ip"], 22),
            ("fly_dns", 10, "fdaa::3", 53),
            ("fly_machines_api", 10, "fdaa::3", 4280),
            ("unique_local", 10, "fd00::1", 80),
            ("ten", 2, "10.0.0.1", 80),
            ("one_seven_two", 2, "172.16.0.1", 80),
            ("one_nine_two", 2, "192.168.0.1", 80),
            ("link_local", 2, "169.254.169.254", 80),
            ("shared_address", 2, "100.64.0.1", 80),
        ]
        public = [
            ("public_v4", 2, "1.1.1.1", 443),
            ("public_v6", 10, "2606:4700:4700::1111", 443),
        ]
        owner = await asyncio.to_thread(live.workspace.repository.get, live.row["id"])
        assert owner["internet_access"] is True
        before = await firewall_hits(owner["resource"])
        script = shlex.quote(REACH)
        arguments = shlex.quote(json.dumps(private + public))
        grant = await issue(live)
        # A grant admits one session, so everything runs in this one.
        async with await connect(live, grant) as conn:
            reached = await conn.run(
                f"python3 -c {script} {arguments} example.com && {PUBLIC_NAME}", check=False
            )
        assert reached.exit_status == 0, reached.stderr
        report, named = reached.stdout.splitlines()
        seen = json.loads(report)
        print("INTERNAL_NETWORK_BOUNDARY " + json.dumps(seen), flush=True)
        assert seen["uid"] == 1000
        for name, *_ in public:
            assert seen[name] == "OPEN", (name, seen)
        assert named == b"OPEN"
        for name, *_ in private:
            assert seen[name] in {"EACCES", "EHOSTUNREACH"}, (name, seen)
        assert await firewall_hits(owner["resource"]) >= before + len(private)
