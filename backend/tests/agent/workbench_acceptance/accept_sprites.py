"""Opt-in hosted Sprites acceptance; the shared scenario exercises real product entry."""

import asyncio
import os

import pytest

from src.platform.sandbox.providers.sprites.provider import SpritesRuntimeProvider, load_bundle
from src.platform.workspace.execution.workbench_host import WorkbenchChannel
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
        os.environ.get("CLOUD_AGENT_TEST_SPRITES") != "1", reason="explicit Sprites opt-in required"
    ),
]


@pytest.fixture(scope="session", autouse=True)
def sprites_configuration():
    provider = SpritesRuntimeProvider()
    artifact = provider.artifact(None)
    provider.validate(artifact)
    load_bundle(artifact)


class SpritesProbe:
    def __init__(self):
        self.provider = SpritesRuntimeProvider()
        self.cold_observed = False
        self.initial_boot = None

    egress_probe = """
import os, socket
assert os.getuid() == 1000
for path in ['/.sprite/api.sock', '/run/puppyone-sprites/api.sock']:
    try:
        with socket.socket(socket.AF_UNIX) as management:
            management.connect(path)
    except PermissionError:
        pass
    else:
        raise AssertionError('Task can access Sprites management socket')
for family, host in [(socket.AF_INET, '1.1.1.1'), (socket.AF_INET6, '2606:4700:4700::1111')]:
    with socket.socket(family, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        try:
            connection.connect((host, 80))
            connection.sendall(b'GET / HTTP/1.0\\r\\nHost: one.one.one.one\\r\\n\\r\\n')
            assert not connection.recv(100), 'Unrestricted task egress'
        except OSError:
            pass
"""

    async def verify_egress(self, resource, receipt):
        policy = await self.provider.transport.request(
            "GET", self.provider._path(resource) + "/policy/network"
        )
        assert policy["rules"] == [{"domain": "*", "action": "deny"}]
        self.initial_boot = (
            await self.provider._exec(resource, "cat", "/proc/sys/kernel/random/boot_id")
        ).strip()
        receipt["egress"] = "UID firewall and management socket isolation exercised by Agent task"

    async def assert_single_resource(self, resource, project):
        assert resource["project_id"] == project
        row = await self.provider.inspect(resource)
        assert row["id"] == resource["sprites_id"]

    async def assert_paused(self, resource):
        row = await eventually(
            lambda: self.provider.inspect(resource),
            lambda value: value["status"] in {"warm", "cold"} and value.get("last_running_at"),
            timeout=120,
        )
        if os.environ.get("CLOUD_AGENT_TEST_SPRITES_COLD") == "1":
            # Metadata GET does not wake compute. Let the real platform reach
            # cold; a service restart is not evidence of a cloud cold wake.
            async with asyncio.timeout(1800):
                while row["status"] != "cold" or not row.get("last_running_at"):
                    await asyncio.sleep(10)
                    row = await self.provider.inspect(resource)
            self.cold_observed = True
            print("SPRITES_COLD_OBSERVED", flush=True)

    async def cleanup(self, case, receipt):
        rows = (
            case.client.table("cloud_workbenches")
            .select("*")
            .eq("project_id", case.project)
            .execute()
            .data
        )
        deleted = []
        receipt["deleted_sprites"] = deleted
        for row in rows:
            if (row.get("binding") or {}).get("provider") != "sprites":
                continue
            # initialize() reconstructs the same name even after a lost create
            # response. cleanup() verifies all ownership labels before deletion.
            host = WorkbenchChannel(row)
            try:
                if self.cold_observed and receipt["result"] == "runtime_passed_cleanup_pending":
                    resumed_boot = (
                        await self.provider._exec(
                            host.resource, "cat", "/proc/sys/kernel/random/boot_id"
                        )
                    ).strip()
                    receipt["cold_boot_changed"] = bool(
                        self.initial_boot and resumed_boot and self.initial_boot != resumed_boot
                    )
            finally:
                await self.provider.cleanup(host.resource)
                await eventually(
                    lambda identity=host.resource["resource_id"]: self.provider.resource_absent(
                        identity
                    ),
                    bool,
                    timeout=60,
                )
                deleted.append(host.resource["resource_id"])
        receipt["cold_observed"] = self.cold_observed
        receipt["cleanup"] = "test Project's owned Sprites absent; verified with metadata GET"
        if self.cold_observed and receipt["result"] == "runtime_passed_cleanup_pending":
            assert receipt.get("cold_boot_changed"), "Cold acceptance requires a new kernel boot"


async def test_sprites_chat_live_files_publication_and_resume(entry, monkeypatch):
    await accept_runtime(entry, monkeypatch, SpritesProbe())
