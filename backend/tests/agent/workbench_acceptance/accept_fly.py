"""Opt-in real Fly chain. Never substitutes provider, Pi, dispatcher or Git.

Run explicitly with CLOUD_AGENT_TEST_FLY=1 and CLOUD_AGENT_FLY_{APP,TOKEN,IMAGE,CLI}.
Only model responses are deterministic. Metering is disabled by the owned
infrastructure fixture: this proves runtime integration, not billing acceptance.
"""

import asyncio
import base64
import json
import os
from uuid import UUID

import httpx
import pytest

from src.config import settings
from src.platform.sandbox.providers.fly.naming import dedicated
from src.platform.sandbox.providers.fly.provider import FlyRuntimeProvider
from tests.agent.workbench_acceptance.accept_production_entry import (
    control_api as control_api,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    entry as entry,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    object_endpoint as object_endpoint,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    postgres as postgres,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    services as services,
)
from tests.agent.workbench_acceptance.provider_scenario import accept_runtime, eventually

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("CLOUD_AGENT_TEST_FLY") != "1", reason="explicit Fly opt-in required"
    ),
]


@pytest.fixture(scope="session", autouse=True)
def fly_configuration():
    # Fail before opening owned infrastructure or allocating a billable resource.
    missing = [
        name
        for name in ("CLOUD_AGENT_FLY_APP", "CLOUD_AGENT_FLY_TOKEN", "CLOUD_AGENT_FLY_IMAGE")
        if not getattr(settings, name)
    ]
    if missing:
        pytest.fail("Live Fly test requires: " + ", ".join(missing))
    FlyRuntimeProvider().validate(settings.CLOUD_AGENT_FLY_IMAGE)


async def firewall_hits(resource):
    """Read the provider firewall counter; product files still use their API."""
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
        "nft -j list chain inet puppyone_task_egress output",
        env={**os.environ, "FLY_API_TOKEN": settings.CLOUD_AGENT_FLY_TOKEN},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(45):
            stdout, _stderr = await process.communicate()
        assert process.returncode == 0, "Unable to read Fly firewall counter"
        return sum(
            expression["counter"]["packets"]
            for item in json.loads(stdout)["nftables"]
            for expression in item.get("rule", {}).get("expr", [])
            if "counter" in expression
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def app_absent(provider, resource):
    try:
        await provider._request(resource, "GET", "")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return True
        raise
    return False


async def cleanup_owned(case, provider, receipt):
    """Delete only resources identified by this new test Project/generations.

    Cleanup also resolves lost create replies and an unattached disk created
    before Machine allocation failed. Production retains disks; this explicit
    test teardown owns and discards its disposable files.
    """
    rows = (
        case.client.table("cloud_workbenches")
        .select("*")
        .eq("project_id", case.project)
        .execute()
        .data
    )
    rows = [row for row in rows if row["resource"]]
    # A Project holds several machines, and the wait below is for all of them.
    for row in rows:
        if dedicated(row["resource"]):
            await provider.destroy_workspace(row["resource"])
            continue
        for resolved in await provider.resolve(row["resource"]):
            await provider.cleanup(resolved)
    for row in rows:
        resource = row["resource"]
        if dedicated(resource):
            await eventually(lambda resource=resource: app_absent(provider, resource), bool)
            continue
        await eventually(
            lambda resource=resource: provider._request(resource, "GET", "/machines"),
            lambda machines: (
                not any(
                    m.get("config", {}).get("metadata", {}).get("project_id") == case.project
                    for m in machines
                )
            ),
            timeout=60,
        )
        name = "pw_" + base64.b32encode(UUID(row["generation"]).bytes).decode().rstrip("=").lower()
        volumes = await eventually(
            lambda resource=resource: provider._request(resource, "GET", "/volumes"),
            lambda volumes, name=name: all(
                v["name"] != name or not v.get("attached_machine_id") for v in volumes
            ),
            timeout=60,
        )
        for volume in volumes:
            if volume["name"] != name:
                continue
            assert not volume.get("attached_machine_id"), "Owned disk still attached; retaining"
            await provider._request(resource, "DELETE", "/volumes/" + volume["id"])
        deleted = await eventually(
            lambda resource=resource: provider._request(resource, "GET", "/volumes"),
            # Fly soft-deletes Volumes; pending_destroy stays in the raw API
            # list but is no longer attachable or billed (Fly billing docs).
            # scheduling_destroy comes first and can outlast this wait.
            lambda volumes, name=name: all(
                v["name"] != name
                or (
                    v["state"] in {"scheduling_destroy", "pending_destroy"}
                    and not v.get("attached_machine_id")
                )
                for v in volumes
            ),
            timeout=60,
        )
        receipt.setdefault("deleted_volumes", []).extend(
            {"id": v["id"], "state": v["state"]} for v in deleted if v["name"] == name
        )
    receipt["cleanup"] = "owned Machines absent; Volumes deleted (absent or pending_destroy)"


class FlyProbe:
    def __init__(self):
        self.provider = FlyRuntimeProvider()

    egress_probe = """
import errno, os, socket
assert os.getuid() == 1000
for family, host in [(socket.AF_INET, '1.1.1.1'), (socket.AF_INET6, '2606:4700:4700::1111')]:
    with socket.socket(family, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        try:
            connection.connect((host, 443))
        except OSError as error:
            # ICMP admin-prohibited maps to EHOSTUNREACH for IPv4 on Fly.
            assert error.errno in (errno.EACCES, errno.EPERM, errno.EHOSTUNREACH), repr(error)
        else:
            raise AssertionError('Unrestricted task egress: ' + host)
"""

    async def verify_egress(self, resource, receipt):
        receipt["firewall_rejected_packets"] = await firewall_hits(resource)
        assert receipt["firewall_rejected_packets"] >= 2

    async def assert_single_resource(self, resource, project):
        machines = await self.provider._request(resource, "GET", "/machines")
        owned = [
            m["id"]
            for m in machines
            if m.get("config", {}).get("metadata", {}).get("project_id") == project
        ]
        assert owned == [resource["resource_id"]]

    async def assert_paused(self, resource):
        assert (await self.provider.inspect(resource))["state"] == "stopped"

    async def cleanup(self, case, receipt):
        await cleanup_owned(case, self.provider, receipt)


async def test_fly_chat_live_files_publication_and_resume(entry, monkeypatch):
    await accept_runtime(entry, monkeypatch, FlyProbe())
