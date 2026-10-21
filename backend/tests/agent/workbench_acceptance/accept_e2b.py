"""Opt-in E2B acceptance of the same product scenario as Fly; no deployment switch."""

import os
import shlex
from uuid import uuid4

import httpx
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
from tests.agent.workbench_acceptance.provider_scenario import accept_runtime, eventually

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
        await assert_absent(sandbox.sandbox_id)
    await eventually(lambda: owned_sandboxes(case.project), lambda items: not items, timeout=30)
    receipt["deleted_sandboxes"] = [item.sandbox_id for item in sandboxes]
    receipt["cleanup"] = "owned E2B sandboxes absent (GET and metadata listing verified)"


async def assert_absent(sandbox_id):
    async def missing():
        try:
            await AsyncSandbox.get_info(sandbox_id, api_key=settings.E2B_API_KEY)
        except NotFoundException:
            return True
        return False

    await eventually(missing, bool, timeout=30)


class E2BProbe:
    def __init__(self):
        self.provider = E2BRuntimeProvider()

    egress_probe = """
import os, socket, ssl
assert os.getuid() == 1000
for family, host in [(socket.AF_INET, '1.1.1.1'), (socket.AF_INET6, '2606:4700:4700::1111')]:
    for port in [80, 443]:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            try:
                connection.connect((host, port))
                if port == 443:
                    connection = ssl.create_default_context().wrap_socket(connection, server_hostname='one.one.one.one')
                connection.sendall(b'GET / HTTP/1.1\\r\\nHost: one.one.one.one\\r\\nConnection: close\\r\\n\\r\\n')
                response = connection.recv(100)
                assert not response, 'Unrestricted task egress: ' + host
            except OSError:
                pass
"""

    async def verify_egress(self, resource, receipt):
        info = await AsyncSandbox.get_info(resource["resource_id"], api_key=settings.E2B_API_KEY)
        assert info.allow_internet_access is False
        assert info.lifecycle["auto_resume"] is False
        assert info.network["allow_public_traffic"] is False
        receipt["egress"] = (
            "provider internet disabled; IPv4/IPv6 HTTP/TLS probes cannot receive origin data"
        )

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


async def test_e2b_network_proxy_denies_origin_data_with_positive_control():
    # E2B's transparent proxy can accept TCP before enforcing egress. A
    # successful connect() alone is neither access nor an isolation failure.
    sandbox = await AsyncSandbox.create(
        template=settings.CLOUD_AGENT_E2B_TEMPLATE,
        timeout=120,
        allow_internet_access=False,
        network={"allow_public_traffic": False},
        lifecycle={"auto_resume": False},
        metadata={"acceptance": "e2b-network-control", "project_id": str(uuid4())},
        api_key=settings.E2B_API_KEY,
    )
    try:
        # An Agent-started server must not become an anonymous public preview.
        await sandbox.commands.run(
            "python3 -m http.server 32123 --bind 0.0.0.0 --directory /tmp",
            user="node",
            background=True,
            timeout=0,
        )
        async with httpx.AsyncClient(timeout=10) as http:
            url = "https://" + sandbox.get_host(32123)
            assert (await http.get(url)).status_code == 403
            response = await eventually(
                lambda: http.get(
                    url, headers={"e2b-traffic-access-token": sandbox.traffic_access_token}
                ),
                lambda response: response.status_code == 200,
                timeout=20,
            )
            assert response.status_code == 200
        await sandbox.commands.run(
            "python3 -c " + shlex.quote(E2BProbe.egress_probe), user="node", timeout=30
        )
        await sandbox.update_network({"allow_out": ["1.1.1.1"], "deny_out": ["0.0.0.0/0"]})
        positive = """
import socket, ssl
for port in [80,443]:
    connection = socket.create_connection(('1.1.1.1', port), timeout=5)
    if port == 443:
        connection = ssl.create_default_context().wrap_socket(connection, server_hostname='one.one.one.one')
    with connection:
        connection.sendall(b'GET / HTTP/1.1\\r\\nHost: one.one.one.one\\r\\nConnection: close\\r\\n\\r\\n')
        assert connection.recv(100).startswith(b'HTTP/1.1 ')
"""
        await sandbox.commands.run("python3 -c " + shlex.quote(positive), user="node", timeout=30)
        await sandbox.update_network({"allow_out": [], "deny_out": ["0.0.0.0/0"]})
        await sandbox.commands.run(
            "python3 -c " + shlex.quote(E2BProbe.egress_probe), user="node", timeout=30
        )
    finally:
        await sandbox.kill()
        await assert_absent(sandbox.sandbox_id)
