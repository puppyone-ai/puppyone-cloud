"""One Agent's work, end to end: its own machine, saved on sleep, reviewed, published.

Real Docker machines, real SSH and the real Git host. Only login and billing are fixtures.
"""

import asyncio
import json

import pytest

from src.platform.access.grant_repository import AccessRepository, GrantRepository
from src.platform.access.grants import GrantService
from src.platform.access.sessions import AccessSessions
from tests.agent.runtime.test_access_lifecycle import issue as issue_registered
from tests.agent.runtime.test_access_lifecycle import setup_access
from tests.agent.runtime.test_hosted_ssh_live import (  # noqa: F401
    agent_machines,
    connect,
    live,
    read_file,
    until_released,
)
from tests.agent.runtime.test_supervisor import native_write
from tests.agent.runtime.test_supervisor import prepared as prepared_fixture

prepared = prepared_fixture
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def project_file(case, path):
    grant = case.admission.authorization.resolve_project_grant(case.project, case.user)
    with case.ops.open_read(case.project, grant) as reader:
        return reader.read_file(case.project, path)


@pytest.mark.parametrize("prepared", ["native"], indirect=True)
async def test_an_agents_work_reaches_the_project_through_its_own_branch(prepared, live):  # noqa: F811
    case = prepared
    await native_write(case, "seed.md", b"seed")
    client = live.service.grants.client
    live.service.authority = GrantService(GrantRepository(client))
    live.service.sessions = AccessSessions(AccessRepository(client))
    context = await asyncio.to_thread(setup_access, live.db, live.args, workspace=live.row)
    machine = json.loads(
        await asyncio.to_thread(
            live.db.sql,
            f"SELECT to_jsonb(public.access_grant_agent_workspace('{context['grant']}'))",
        )
    )
    workspace = live.workspace
    repository = workspace.repository
    transport = case.publication.transport
    async with agent_machines(live, transport):
        await asyncio.wait_for(
            workspace.ensure_running(
                {"user_id": machine["user_id"], "project_id": machine["project_id"]}, machine
            ),
            120,
        )
        ticket, _, _ = await asyncio.to_thread(
            issue_registered,
            live.db,
            context,
            channel="ssh",
            public_key=live.key.export_public_key().decode().strip(),
        )
        async with await connect(live, {**ticket, "username": ticket["id"]}) as conn:
            await conn.run("printf 'from the agent\n' > agent.md", check=True)
        await until_released(live, ticket["id"])

        # The owner's machine cannot see the Agent's file until it is brought in.
        hidden = await live.api.get(
            f"/api/v1/projects/{live.args['project']}/workbench/file",
            params={"generation": live.row["generation"], "path": "agent.md"},
        )
        assert hidden.status_code == 404

        # What maintenance does once the machine has been idle.
        await asyncio.to_thread(
            live.db.sql,
            f"""UPDATE cloud_workbenches SET last_activity_at=now()-interval '1 day'
            WHERE id='{machine["id"]}'""",
        )
        row = await asyncio.to_thread(repository.get, machine["id"])
        claimed = await asyncio.to_thread(repository.idle, row)
        assert claimed["acquired"], claimed
        await workspace.lifecycle.pause(claimed["workbench"])

        saved = await asyncio.to_thread(repository.get, machine["id"])
        assert saved["state"] == "paused"
        assert saved["unpublished_changes"] is False
        assert saved["target_ref"].startswith("refs/heads/agents/")
        grant = case.admission.authorization.resolve_project_grant(case.project, case.user)
        branch = await asyncio.to_thread(
            transport.describe, case.project, grant, target_ref=saved["target_ref"]
        )
        assert branch["expected_oid"]
        with pytest.raises(FileNotFoundError):
            await asyncio.to_thread(project_file, case, "agent.md")

        owner = await asyncio.to_thread(repository.get, live.row["id"])
        brought = await workspace.git.sync(owner, saved["target_ref"])
        assert brought["status"] in {"fast_forward", "merged"}, brought
        assert await read_file(live, "agent.md") == b"from the agent\n"

        published = await workspace.git.publish(owner)
        assert published["status"] == "published", published
        content = await asyncio.to_thread(project_file, case, "agent.md")
        assert content == b"from the agent\n"
