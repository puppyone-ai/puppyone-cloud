"""Opt-in E2B acceptance of the same product scenario as Fly; no deployment switch."""

import os

import pytest
from e2b import AsyncSandbox
from e2b.exceptions import NotFoundException
from e2b.sandbox.sandbox_api import SandboxQuery

from src.config import settings
from src.platform.scope_sandbox.execution.providers.e2b import E2BRuntimeProvider
from tests.agent.workbench_acceptance.accept_production_entry import control_api as control_api
from tests.agent.workbench_acceptance.accept_production_entry import entry as entry
from tests.agent.workbench_acceptance.accept_production_entry import (
    object_endpoint as object_endpoint,
)
from tests.agent.workbench_acceptance.accept_production_entry import postgres as postgres
from tests.agent.workbench_acceptance.accept_production_entry import services as services
from tests.agent.workbench_acceptance.provider_scenario import accept_runtime

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("CLOUD_AGENT_TEST_E2B") != "1", reason="explicit E2B opt-in required"
    ),
]


@pytest.fixture(scope="session", autouse=True)
def e2b_configuration():
    E2BRuntimeProvider().validate(settings.CLOUD_AGENT_E2B_TEMPLATE)


async def owned_sandboxes(project):
    pager = AsyncSandbox.list(
        query=SandboxQuery(metadata={"project_id": project}), api_key=settings.E2B_API_KEY
    )
    result = []
    while pager.has_next:
        result.extend(await pager.next_items())
    assert all(item.metadata.get("project_id") == project for item in result)
    return result


async def cleanup_owned(case, provider, receipt):
    # Include an allocation whose response was lost, but never delete another
    # Project's resource. This Project was created by the owned test fixture.
    sandboxes = await owned_sandboxes(case.project)
    for sandbox in sandboxes:
        await AsyncSandbox.kill(sandbox.sandbox_id, api_key=settings.E2B_API_KEY)
        with pytest.raises(NotFoundException):
            await AsyncSandbox.get_info(sandbox.sandbox_id, api_key=settings.E2B_API_KEY)
    assert await owned_sandboxes(case.project) == []
    receipt["deleted_sandboxes"] = [item.sandbox_id for item in sandboxes]
    receipt["cleanup"] = "owned E2B sandboxes absent (GET and metadata listing verified)"


class E2BProbe:
    def __init__(self):
        self.provider = E2BRuntimeProvider()

    egress_probe = """
import os, socket
assert os.getuid() == 1000
for family, host in [(socket.AF_INET, '1.1.1.1'), (socket.AF_INET6, '2606:4700:4700::1111')]:
    with socket.socket(family, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        try:
            connection.connect((host, 443))
        except OSError:
            pass
        else:
            raise AssertionError('Unrestricted task egress: ' + host)
"""

    async def verify_egress(self, resource, receipt):
        info = await AsyncSandbox.get_info(resource["resource_id"], api_key=settings.E2B_API_KEY)
        assert info.allow_internet_access is False
        assert info.lifecycle["auto_resume"] is False
        receipt["egress"] = "provider internet disabled; IPv4/IPv6 TCP probes denied"

    async def assert_single_resource(self, resource, project):
        assert [item.sandbox_id for item in await owned_sandboxes(project)] == [
            resource["resource_id"]
        ]

    async def assert_paused(self, resource):
        info = await AsyncSandbox.get_info(resource["resource_id"], api_key=settings.E2B_API_KEY)
        assert str(info.state) == "paused"

    async def cleanup(self, case, receipt):
        await cleanup_owned(case, self.provider, receipt)


async def test_e2b_chat_live_files_publication_and_resume(entry, monkeypatch):
    await accept_runtime(entry, monkeypatch, E2BProbe())
