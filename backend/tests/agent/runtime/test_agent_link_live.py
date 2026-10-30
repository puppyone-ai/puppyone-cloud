"""An Agent follows a link with stock curl, ssh-keygen and ssh; nothing is mocked but login.

The commands are taken from the page the Agent reads and executed as written, so
the instructions cannot drift from the gateway, the API or the workspace.
"""

import asyncio
import base64
import json
import re
import secrets
import shlex
import socket
import subprocess
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from src.config import settings
from src.platform.access import agent_link_router as routes
from src.platform.access.agent_links import AgentLinkRepository, AgentLinkService
from src.platform.access.grant_repository import AccessRepository, GrantRepository
from src.platform.access.grants import GrantService
from src.platform.access.sessions import AccessSessions
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from tests.agent.runtime.test_hosted_ssh_live import live, read_file  # noqa: F401

pytestmark = pytest.mark.integration


@asynccontextmanager
async def stack(live, monkeypatch):  # noqa: F811
    client = live.service.grants.client
    links = AgentLinkService(AgentLinkRepository(client), endpoint=live.service.endpoint)
    live.service.agent_links = links
    live.service.authority = GrantService(GrantRepository(client))
    live.service.sessions = AccessSessions(AccessRepository(client))
    monkeypatch.setattr(settings, "ACCESS_LIFECYCLE_ENABLED", True)
    monkeypatch.setattr(settings, "ACCESS_CREDENTIAL_HASH_SECRET", "agent-link-test-secret")
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://web.fixture")
    # An Agent's first connection starts a machine, so the sandbox module has to be
    # running. The Project is empty here: a Workspace initializes from no base.
    workspace = live.workspace
    def describe(project, grant, target_ref=None):
        return {"object_format": "sha1", "target_ref": target_ref or "refs/heads/main", "expected_oid": None}
    workspace.transport = workspace.lifecycle.transport = SimpleNamespace(describe=describe)
    meter = SimpleNamespace(
        ensure_session=AsyncMock(),
        check_session=AsyncMock(),
        detach_session=AsyncMock(),
        resume_session=AsyncMock(),
        finish_session=AsyncMock(return_value=True),
    )
    workspace.billing = workspace.lifecycle.billing = meter
    stop_lifecycle = asyncio.Event()
    lifecycle = asyncio.create_task(workspace.lifecycle.serve(stop=stop_lifecycle))
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.instructions_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=live.args["user"], role="authenticated", is_anonymous=False
    )
    app.dependency_overrides[routes.agent_link_service] = lambda: links
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    monkeypatch.setattr(settings, "PUBLIC_URL", origin)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.02)
        async with httpx.AsyncClient(base_url=origin, timeout=10) as owner:
            yield owner
    finally:
        server.should_exit = True
        await serving
        stop_lifecycle.set()
        await asyncio.wait_for(lifecycle, 15)
        await cleanup_agent_machines(live)


async def cleanup_agent_machines(live):
    project = live.args["project"]
    rows = await asyncio.to_thread(
        live.db.sql,
        f"""SELECT coalesce(string_agg(resource->>'resource_id',' '),'') FROM cloud_workbenches
        WHERE project_id='{project}' AND agent_registration_id IS NOT NULL""",
    )
    for machine in rows.split():
        await asyncio.to_thread(subprocess.run, ["docker", "rm", "-f", machine], capture_output=True)


async def run(command, *, check=True):
    """The Agent's shell: one command line, in its own process, with no help from us."""
    result = await asyncio.to_thread(
        subprocess.run, ["bash", "-c", command], capture_output=True, text=True, timeout=60
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


def new_link():
    return "pai_" + secrets.token_urlsafe(32)


def blocks(page):
    """Commands exactly as the page gives them, in order."""
    return [line.strip() for line in page.splitlines() if line.startswith("    ")]


async def agent_applies(owner, tmp_path, name, secret):
    """Run steps 1 and 2 of the page, pointing only ``~/.ssh`` at this test's folder."""
    response = await owner.get(f"/agent/{secret}")
    assert "text/plain" in response.headers["content-type"]
    page = response.text
    commands = blocks(page)
    keys, known = tmp_path / name, tmp_path / f"{name}_known_hosts"
    keys.mkdir()
    home = lambda text: text.replace("~/.ssh", str(keys))  # noqa: E731
    create = next(command for command in commands if "ssh-keygen" in command)
    await run(home(commands[0]))
    await run(home(create))
    await run(home(create))  # Following the page twice keeps the same key.
    apply = next(command for command in commands if "/connect " in command)
    reply = await run(home(apply).replace("YOUR AGENT NAME", name))
    return json.loads(reply.stdout), home, known, page


def ssh_line(access, home, known):
    command = home(access["command"]).replace(
        home("~/.ssh/puppyone_known_hosts"), str(known)
    )
    return command + " -o BatchMode=yes"


async def test_the_page_alone_connects_an_agent_through_a_sign_in(live, tmp_path, monkeypatch):  # noqa: F811
    link = new_link()
    async with stack(live, monkeypatch) as owner:
        created = await owner.post(
            f"/api/v1/projects/{live.args['project']}/agent-links",
            json={
                "id": str(uuid4()),
                "secret": link,
                "workbench_id": live.row["id"],
                "admission": "sign_in",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["agent_url"].endswith(f"/agent/{link}")
        assert "key_hash" not in created.json()

        reply, home, known, page = await agent_applies(owner, tmp_path, "agent-one", link)
        assert reply["status"] == "pending"
        identity = reply["connection_id"]
        assert reply["sign_in_url"] == f"https://web.fixture/agent-connect/{identity}"

        poll = next(line for line in blocks(page) if "CONNECTION_ID" in line)
        poll = poll.replace("CONNECTION_ID", identity).replace("POLL_SECRET", reply["poll_secret"])
        assert json.loads((await run(poll)).stdout)["status"] == "pending"

        review = await owner.get(f"/api/v1/agent-links/connections/{identity}/review")
        assert review.json()["name"] == "agent-one"
        decided = await owner.post(
            f"/api/v1/agent-links/connections/{identity}/decision", json={"decision": "approve"}
        )
        assert decided.status_code == 200, decided.text

        ready = json.loads((await run(poll)).stdout)
        assert ready["status"] == "ready"
        access = ready["ssh"]
        await run(access["install_known_hosts"].replace("~/.ssh/puppyone_known_hosts", str(known)))
        command = ssh_line(access, home, known)

        # The same stable line works for every command, with no renewal.
        first = await run(command + " " + shlex.quote("pwd && echo written-by-agent > agent.txt"))
        assert first.stdout.strip() == "/workspace/repo"
        again = await run(command + " " + shlex.quote("cat agent.txt"))
        assert again.stdout.strip() == "written-by-agent"
        # The Agent is on its own machine: nothing crosses until Git carries it.
        files = f"/api/v1/projects/{live.args['project']}/workbench/file"
        generation = {"generation": live.row["generation"]}
        hidden = await live.api.get(files, params={**generation, "path": "agent.txt"})
        assert hidden.status_code == 404
        written = await live.api.put(
            files,
            params={**generation, "path": "owner.txt"},
            json={
                "path": "owner.txt",
                "content_base64": base64.b64encode(b"owner").decode(),
                "expected_revision": None,
            },
        )
        assert written.status_code == 200, written.text
        assert (await read_file(live, "owner.txt")) == b"owner"
        unseen = await run(command + " " + shlex.quote("test -e owner.txt"), check=False)
        assert unseen.returncode == 1
        machine = await asyncio.to_thread(
            live.db.row,
            f"""SELECT resource->>'resource_id' AS machine, target_ref FROM cloud_workbenches
            WHERE project_id='{live.args['project']}' AND agent_registration_id IS NOT NULL""",
        )
        assert machine["machine"] and machine["machine"] != live.row["resource"]["resource_id"]
        assert machine["target_ref"].startswith("refs/heads/agents/")

        # A different key on the same stable user is refused.
        stranger = tmp_path / "stranger"
        await run(f"ssh-keygen -q -t ed25519 -N '' -f {stranger}")
        refused = await run(
            re.sub(r"-i \S+", f"-i {stranger}", command) + " true", check=False
        )
        assert refused.returncode == 255
        assert "Permission denied" in refused.stderr

        listed = await owner.get(f"/api/v1/projects/{live.args['project']}/agent-links")
        assert [item["name"] for item in listed.json()["connections"]] == ["agent-one"]

        stopped = await owner.delete(f"/api/v1/projects/{live.args['project']}/agent-links")
        assert stopped.status_code == 200, stopped.text
        ended = await run(command + " true", check=False)
        assert ended.returncode == 255
        assert json.loads((await run(poll)).stdout)["status"] == "revoked"


async def test_the_page_alone_connects_an_agent_from_a_link(live, tmp_path, monkeypatch):  # noqa: F811
    link = new_link()
    monkeypatch.setattr(settings, "AGENT_OPEN_LINKS_ENABLED", True)
    async with stack(live, monkeypatch) as owner:
        created = await owner.post(
            f"/api/v1/projects/{live.args['project']}/agent-links",
            json={
                "id": str(uuid4()),
                "secret": link,
                "workbench_id": live.row["id"],
                "admission": "link",
            },
        )
        assert created.status_code == 200, created.text
        reply, home, known, _ = await agent_applies(owner, tmp_path, "agent-two", link)
        assert reply["status"] == "ready"
        access = reply["ssh"]
        await run(access["install_known_hosts"].replace("~/.ssh/puppyone_known_hosts", str(known)))
        result = await run(ssh_line(access, home, known) + " " + shlex.quote("echo connected"))
        assert result.stdout.strip() == "connected"


async def test_a_page_is_only_served_for_a_well_formed_link(live, monkeypatch):  # noqa: F811
    async with stack(live, monkeypatch) as owner:
        assert (await owner.get("/agent/not-a-link")).status_code == 404
        response = await owner.get(f"/agent/{new_link()}")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-robots-tag"] == "noindex"
