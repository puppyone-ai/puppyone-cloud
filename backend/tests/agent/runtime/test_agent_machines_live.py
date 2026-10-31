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
from tests.agent.runtime.test_access_lifecycle import issue as issue_registered
from tests.agent.runtime.test_access_lifecycle import setup_access
from tests.agent.runtime.test_hosted_ssh_live import (  # noqa: F401
    PROVIDER,
    agent_machines,
    connect,
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

        slept = await put_to_sleep(live, machine["id"])
        await asyncio.to_thread(
            live.db.sql,
            f"""UPDATE cloud_workbenches SET paused_at=now()-interval '8 days',
            maintenance_after=now()-interval '1 second' WHERE id='{machine["id"]}'""",
        )
        await live.workspace.lifecycle.reap()
        retired = await until_state(live, machine["id"], "retired")
        if PROVIDER == "fly":
            machines = await live.owner.runtime._request(first["resource"], "GET", "/machines")
            assert first["resource"]["resource_id"] not in {item["id"] for item in machines}

        rebuilt = await start(live, retired)
        assert rebuilt["id"] == machine["id"]
        assert rebuilt["generation"] != first["generation"]
        assert rebuilt["resource"]["resource_id"] != first["resource"]["resource_id"]
        assert rebuilt["target_ref"] == first["target_ref"]
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

        marked = await shell(live, second, "printf theirs > only-theirs.txt")
        assert marked.exit_status == 0, marked.stderr
        looked = await shell(live, first, "test ! -e only-theirs.txt")
        assert looked.exit_status == 0
