"""Unit tests for Tier 2 (fly_host_export) lifecycle, networking, mount, pause & revocation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from src.config import settings
from src.platform.access.adapters.ssh.hosted import HostedSshAccess
from src.platform.repository_policy import reset_policy_cache
from src.platform.sandbox.providers.fly.naming import (
    dedicated,
    isolated_app,
    shared_dedicated_network,
    workspace_app,
)
from src.platform.sandbox.providers.fly.provider import FlyRuntimeProvider, _transport_app
from src.platform.sandbox.providers.fly.ssh import FlySSHConnections
from src.platform.workspace.collaboration import PRE_AGENT_BACKUP_MESSAGE, FlyHostExportTopology
from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.execution.runtime_contract import FILE_PROTOCOL, RUNTIME_PROTOCOL
from src.platform.workspace.execution.workbench_host import WorkbenchChannel
from src.platform.workspace.lifecycle import WorkbenchLifecycle

VALID_RUNTIME = {
    "runtime_protocol": RUNTIME_PROTOCOL,
    "file_protocol": FILE_PROTOCOL,
    "capabilities": ["workspace_lifecycle", "native_files", "file_watch", "workspace_shell"],
    "build_id": "a" * 64,
}


def _set_policy(monkeypatch, name: str) -> None:
    monkeypatch.setattr(settings, "REPOSITORY_POLICY", name)
    monkeypatch.setattr(settings, "REPOSITORY_POLICY_FILE", "")
    reset_policy_cache()


def test_tier2_agent_channel_joins_project_host_dedicated_app_without_owning_it(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_APP", "shared-fly")
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_ORG", "puppy-org")
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_HOST_EXPORT_PROTOCOL", "nfs_ganesha")

    host_id = str(uuid4())
    agent_id = str(uuid4())
    row = {
        "id": agent_id,
        "generation": str(uuid4()),
        "project_id": "proj-1",
        "agent_registration_id": str(uuid4()),
        "is_default": False,
        "collaboration_mode": "fly_host_export",
        "host_workbench_id": host_id,
        "binding": {"provider": "fly", "artifact": "registry/agent@sha256:" + "a" * 64},
        "resource": None,
    }
    ch = WorkbenchChannel(row)
    assert ch.resource["app"] == workspace_app(host_id)
    assert ch.resource["host_workbench_id"] == host_id
    assert ch.workspace_mount_env == {
        "PUPPYONE_WORKSPACE_MOUNT_MODE": "fly_host_export",
        "PUPPYONE_HOST_EXPORT_PROTOCOL": "nfs_ganesha",
    }
    # Crucial security & cleanup invariant:
    # - dedicated(agent_resource) is FALSE so destroying the Agent VM NEVER deletes the Project Fly App!
    # - shared_dedicated_network & isolated_app are TRUE so Fly SSH & transport route to the Project app!
    assert dedicated(ch.resource) is False
    assert shared_dedicated_network(ch.resource) is True
    assert isolated_app(ch.resource) is True
    assert _transport_app(ch.resource) == "shared-fly"


@pytest.mark.asyncio
async def test_destroying_tier2_agent_vm_deletes_only_agent_machine_and_volume_never_project_app(
    monkeypatch,
):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "fly-test-token")
    host_id = str(uuid4())
    agent_id = str(uuid4())
    agent_gen = str(uuid4())
    project_app = workspace_app(host_id)
    resource = {
        "app": project_app,
        "workbench_id": agent_id,
        "workbench_generation": agent_gen,
        "project_id": "proj-1",
        "host_workbench_id": host_id,
        "resource_id": "mach-agent-a",
        "volume_id": "vol-agent-a",
    }
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path.endswith("/machines"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "mach-agent-a",
                        "config": {
                            "metadata": {
                                "workbench_id": agent_id,
                                "workbench_generation": agent_gen,
                                "project_id": "proj-1",
                            }
                        },
                    },
                    {
                        "id": "mach-host",
                        "config": {
                            "metadata": {
                                "workbench_id": host_id,
                                "workbench_generation": str(uuid4()),
                                "project_id": "proj-1",
                            }
                        },
                    },
                ],
            )
        if request.method == "GET" and request.url.path.endswith("/volumes"):
            return httpx.Response(
                200,
                json=[
                    {"id": "vol-agent-a", "name": "pw_agent", "state": "created"},
                    {"id": "vol-host", "name": "pw_host", "state": "created"},
                ],
            )
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        base_url="https://api.machines.dev", transport=httpx.MockTransport(handle)
    ) as client:
        await FlyRuntimeProvider(client=client).destroy_workspace(resource)

    # Never deleted the Project app or Host VM/volume; only deleted Agent A's machine & volume.
    assert ("DELETE", f"/v1/apps/{project_app}") not in calls
    assert ("DELETE", f"/v1/apps/{project_app}/machines/mach-host") not in calls
    assert ("DELETE", f"/v1/apps/{project_app}/volumes/vol-host") not in calls
    assert ("DELETE", f"/v1/apps/{project_app}/machines/mach-agent-a") in calls
    assert ("DELETE", f"/v1/apps/{project_app}/volumes/vol-agent-a") in calls


@pytest.mark.asyncio
async def test_tier2_agent_destroy_treats_already_deleted_host_app_404_as_destroyed(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "fly-test-token")
    host_id = str(uuid4())
    agent_id = str(uuid4())
    agent_gen = str(uuid4())
    project_app = workspace_app(host_id)
    resource = {
        "app": project_app,
        "workbench_id": agent_id,
        "workbench_generation": agent_gen,
        "project_id": "proj-1",
        "host_workbench_id": host_id,
        "resource_id": "mach-agent-a",
        "volume_id": "vol-agent-a",
    }

    # Case 1: Host Fly app was already force-deleted before Agent cleanup runs (GET /machines -> 404).
    async with httpx.AsyncClient(
        base_url="https://api.machines.dev",
        transport=httpx.MockTransport(lambda req: httpx.Response(404, json={"error": "not_found"})),
    ) as client:
        await FlyRuntimeProvider(client=client).destroy_workspace(resource)

    # Case 2: Host Fly app disappears between GET /machines and GET /volumes (GET /volumes -> 404).
    def handle_volumes_404(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/machines"):
            return httpx.Response(200, json=[])
        if request.method == "GET" and request.url.path.endswith("/volumes"):
            return httpx.Response(404, json={"error": "not_found"})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        base_url="https://api.machines.dev",
        transport=httpx.MockTransport(handle_volumes_404),
    ) as client:
        await FlyRuntimeProvider(client=client).destroy_workspace(resource)


@pytest.mark.asyncio
async def test_fly_ssh_allows_tier2_agent_on_project_host_dedicated_app():
    host_id = str(uuid4())
    agent_id = str(uuid4())
    resource = {
        "app": workspace_app(host_id),
        "workbench_id": agent_id,
        "host_workbench_id": host_id,
        "resource_id": "mach-agent-a",
    }
    pool = FlySSHConnections("shared", "token", "fly", {})
    try:
        entry = await pool._reserve(resource, "fdaa:d9::2")
        assert entry.app == workspace_app(host_id)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_tier2_lifecycle_allocates_grants_mounts_and_unmounts_cleanly(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_HOST_EXPORT_PROTOCOL", "nfs_ganesha")
    host_id = str(uuid4())
    agent_id = str(uuid4())
    host_row = {
        "id": host_id,
        "generation": str(uuid4()),
        "project_id": "proj-1",
        "user_id": "owner-1",
        "state": "running",
        "open_requested": False,
        "is_default": True,
        "compute_epoch": 3,
        "resource": {"app": workspace_app(host_id), "resource_id": "mach-host"},
        "binding": {"provider": "fly", "artifact": "registry/agent@sha256:" + "a" * 64},
    }
    agent_row = {
        "id": agent_id,
        "generation": str(uuid4()),
        "project_id": "proj-1",
        "user_id": "owner-1",
        "state": "allocating",
        "is_default": False,
        "agent_registration_id": str(uuid4()),
        "collaboration_mode": "fly_host_export",
        "host_workbench_id": host_id,
        "compute_epoch": 1,
        "resource": None,
        "binding": {"provider": "fly", "artifact": "registry/agent@sha256:" + "a" * 64},
    }
    controls_log = []

    class FakeChannel:
        def __init__(self, row):
            self.row = row
            self.resource = dict(row.get("resource") or {"app": workspace_app(host_id)})
            self.runtime = SimpleNamespace(aclose=AsyncMock())

        async def create(self):
            self.resource.update(resource_id="mach-agent-a", allocation_pending=False)
            return self.resource

        async def connect(self):
            return self

        async def _open_control(self):
            return self

        async def close(self):
            return None

        async def control(self, action, value=None):
            controls_log.append((self.row["id"], action, value))
            if action == "capabilities":
                return {"workbench_lifecycle": 1, "host_export": 1, "runtime": VALID_RUNTIME}
            if action == "host-export-start":
                return {
                    "protocol": "nfs_ganesha",
                    "host_ip": "fdaa:d9::1",
                    "port": 2223,
                    "host_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOV/hostkeyhostkeyhostkeyhostkeyhostkeyhostk",
                    "export_path": "/repo",
                }
            if action == "host-export-client-key":
                return {
                    "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOV/agentkeyagentkeyagentkeyagentkeyagentk",
                    "agent_ip": "fdaa:d9::2",
                }
            if action == "host-export-grant":
                return {"granted": True}
            if action == "host-export-mount":
                return {"mounted": True, "reused": False, "protocol": "nfs_ganesha", "fstype": "nfs4"}
            if action in {"host-export-unmount", "host-export-revoke"}:
                return {"ok": True}
            raise AssertionError(f"Unexpected control {action}")

    repo = SimpleNamespace(
        get=Mock(side_effect=lambda wid: host_row if wid == host_id else agent_row),
        transition=Mock(side_effect=lambda r, state, res=None: {**r, "state": state, "resource": res or r.get("resource")}),
        progress=Mock(),
        ready=Mock(side_effect=lambda r, res: {**r, "state": "running", "resource": res}),
        save_outcome=Mock(),
    )
    service = SimpleNamespace(
        repository=repo,
        transport=SimpleNamespace(),
        filesystem=SimpleNamespace(),
        billing=SimpleNamespace(ensure_session=AsyncMock(), check_session=AsyncMock()),
        channel_factory=FakeChannel,
        providers=Mock(),
        channels=SimpleNamespace(invalidate=AsyncMock()),
        check=AsyncMock(),
        context=Mock(return_value=({}, SimpleNamespace(org_id="org-1"))),
    )
    lifecycle = WorkbenchLifecycle(service)
    lifecycle.ready = AsyncMock()

    agent_channel = FakeChannel(agent_row)
    result = await lifecycle._start_host(agent_row, SimpleNamespace(org_id="org-1"), agent_channel)
    assert result["state"] == "running"
    assert result["resource"]["initialized"] is True
    assert result["resource"]["host_export"]["fstype"] == "nfs4"
    assert result["resource"]["host_export"]["host_compute_epoch"] == 3
    assert result["resource"]["host_export"]["agent_ip"] == "fdaa:d9::2"
    # Verify exact sequence: host-export-start -> client-key -> grant -> mount
    actions = [(wid == host_id, act) for wid, act, _ in controls_log if act != "capabilities"]
    assert actions == [
        (True, "host-export-start"),
        (False, "host-export-client-key"),
        (True, "host-export-grant"),
        (False, "host-export-mount"),
    ]

    # Pause unmounts & revokes without running git.snapshot on Agent VM.
    controls_log.clear()
    await lifecycle._save_work(result, on_pause=True)
    pause_actions = [(wid == host_id, act) for wid, act, _ in controls_log]
    assert pause_actions == [(False, "host-export-unmount"), (True, "host-export-revoke")]
    repo.save_outcome.assert_called_once_with(result, "saved", error=None, oid=None)


@pytest.mark.asyncio
async def test_tier2_before_agent_session_checkpoints_project_host_workspace(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    host_row = {
        "id": "wb-host",
        "project_id": "proj-1",
        "is_default": True,
        "agent_registration_id": None,
        "state": "running",
        "unpublished_changes": True,
    }
    agent_row = {
        "id": "wb-agent",
        "project_id": "proj-1",
        "is_default": False,
        "agent_registration_id": "reg-1",
        "collaboration_mode": "fly_host_export",
        "host_workbench_id": "wb-host",
    }
    repo = SimpleNamespace(
        get=Mock(side_effect=lambda wid: agent_row if wid == "wb-agent" else host_row),
        save_outcome=Mock(),
    )
    git = SimpleNamespace(snapshot=AsyncMock(return_value={"status": "saved", "tip": "b" * 40}))
    workspace = SimpleNamespace(repository=repo, git=git)

    res = await FlyHostExportTopology().before_agent_session(
        workspace, {"workbench_id": "wb-agent", "project_id": "proj-1", "access_grant_id": "g-1"}
    )
    assert res == {"status": "saved", "tip": "b" * 40}
    git.snapshot.assert_awaited_once_with(host_row, message=PRE_AGENT_BACKUP_MESSAGE)


@pytest.mark.asyncio
async def test_hosted_ssh_wakes_project_host_before_tier2_agent_vm():
    host_row = {
        "id": "wb-host",
        "project_id": "proj-1",
        "user_id": "owner-1",
        "state": "paused",
        "open_requested": False,
        "owner_deleted_at": None,
    }
    agent_row = {
        "id": "wb-agent",
        "project_id": "proj-1",
        "user_id": "owner-1",
        "actor_user_id": "owner-1",
        "state": "retired",
        "collaboration_mode": "fly_host_export",
        "host_workbench_id": "wb-host",
    }
    started_order = []

    async def fake_ensure_running(actor, row):
        started_order.append(row["id"])
        return {**row, "state": "running"}

    workspace = SimpleNamespace(
        repository=SimpleNamespace(get=Mock(return_value=host_row)),
        ensure_running=AsyncMock(side_effect=fake_ensure_running),
    )
    links = SimpleNamespace(workspace=AsyncMock(return_value=agent_row))
    access = HostedSshAccess(workspace, grants=SimpleNamespace(), connections=SimpleNamespace(), agent_links=links)

    await access.start_agent_workspace("conn-1")
    assert started_order == ["wb-host", "wb-agent"]
