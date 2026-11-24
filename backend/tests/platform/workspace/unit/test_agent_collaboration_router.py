"""Unit tests for the pluggable multi-agent workspace collaboration topology router."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.config import Settings, settings
from src.platform.repository_policy import reset_policy_cache
from src.platform.sandbox.providers.fly.naming import workspace_app
from src.platform.sandbox.providers.fly.provider import FlyRuntimeProvider
from src.platform.workspace.collaboration import (
    PRE_AGENT_BACKUP_MESSAGE,
    AgentCollaborationRouter,
    FlyHostExportTopology,
    IsolatedBranchTopology,
    JuiceFsSharedTopology,
    SharedMachineTopology,
    WorkspaceMountSpec,
)
from src.platform.workspace.errors import WorkspaceError


def _set_policy(monkeypatch, name: str) -> None:
    monkeypatch.setattr(settings, "REPOSITORY_POLICY", name)
    monkeypatch.setattr(settings, "REPOSITORY_POLICY_FILE", "")
    reset_policy_cache()


def test_router_lists_all_tiers_and_resolves_precedence(monkeypatch):
    router = AgentCollaborationRouter()
    modes = router.list_modes()
    assert [m["mode"] for m in modes] == [
        "shared_machine",
        "fly_host_export",
        "juicefs_shared",
        "isolated_branch",
    ]
    assert [m["implemented"] for m in modes] == [True, True, False, True]

    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")
    assert router.resolve().mode == "shared_machine"
    assert router.resolve("isolated_branch").mode == "isolated_branch"
    # Per-project override wins over explicit caller mode and deployment default.
    assert router.resolve("shared_machine", project_override="isolated_branch").mode == (
        "isolated_branch"
    )
    assert router.resolve(project_override="fly_host_export").mode == "fly_host_export"

    with pytest.raises(WorkspaceError) as exc:
        router.resolve("unknown_mode")
    assert exc.value.status_code == 422
    assert exc.value.code == "agent_collaboration_mode_invalid"


@pytest.mark.parametrize("mode", ["juicefs_shared"])
def test_unimplemented_tiers_fail_closed_on_require_ready(mode):
    router = AgentCollaborationRouter()
    with pytest.raises(WorkspaceError) as exc:
        router.require_ready(mode)
    assert exc.value.status_code == 503
    assert exc.value.detail == {"code": "agent_collaboration_mode_not_ready", "mode": mode}


def test_tier2_fly_host_export_is_ready_and_can_still_be_overridden():
    router = AgentCollaborationRouter()
    active = router.require_ready("fly_host_export")
    assert active.mode == "fly_host_export"
    assert active.isolated_compute_vm is True
    assert active.shared_live_files is True
    assert active.uses_agent_branch is False

    class CustomFlyHostExport(FlyHostExportTopology):
        def instructions_footer(self) -> str:
            return "custom tier2 footer"

    router.register(CustomFlyHostExport())
    assert router.require_ready("fly_host_export").instructions_footer() == "custom tier2 footer"


def test_mount_descriptors_across_all_four_topologies(monkeypatch):
    compute = {"id": "wb-agent", "project_id": "proj-alpha-1", "generation": "gen-a"}
    storage = {
        "id": "wb-shared",
        "project_id": "proj-alpha-1",
        "generation": "gen-s",
        "resource": {"app": "puppyone-ws", "resource_id": "mach-99"},
    }

    assert SharedMachineTopology().resolve_mount(compute, storage) == WorkspaceMountSpec(
        mode="local"
    )
    assert SharedMachineTopology().resolve_mount(compute, storage).worker_env() == {}
    assert IsolatedBranchTopology().resolve_mount(compute, storage).worker_env() == {}

    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_HOST_EXPORT_PROTOCOL", "nfs_ganesha")
    host_mount = FlyHostExportTopology().resolve_mount(compute, storage)
    assert host_mount.mode == "fly_host_export"
    assert host_mount.protocol == "nfs_ganesha"
    assert host_mount.worker_env() == {
        "PUPPYONE_WORKSPACE_MOUNT_MODE": "fly_host_export",
        "PUPPYONE_HOST_EXPORT_PROTOCOL": "nfs_ganesha",
        "PUPPYONE_HOST_EXPORT_TARGET": "mach-99.vm.puppyone-ws.internal",
    }
    with pytest.raises(WorkspaceError) as missing_host:
        FlyHostExportTopology().resolve_mount(compute, None)
    assert missing_host.value.code == "agent_collaboration_host_missing"

    monkeypatch.setattr(settings, "CLOUD_AGENT_JUICEFS_META_PREFIX", "staging")
    jfs_mount = JuiceFsSharedTopology().resolve_mount(compute, storage)
    assert jfs_mount.mode == "juicefs_shared"
    assert jfs_mount.worker_env() == {
        "PUPPYONE_WORKSPACE_MOUNT_MODE": "juicefs_shared",
        "PUPPYONE_JUICEFS_SCHEMA": "jfs_staging_proj_alpha_1",
        "PUPPYONE_JUICEFS_META_URL_FILE": "/run/puppyone-workbench/juicefs-meta.url",
    }


def test_hosted_settings_validate_tier_prerequisites():
    base = {
        "_env_file": None,
        "APP_ENV": "staging",
        "SKIP_AUTH": False,
        "SERVICE_ROLE": "api",
        "JWT_SECRET": "a" * 40,
        "MCP_TOKEN_SECRET": "b" * 40,
        "INTERNAL_API_SECRET": "c" * 40,
        "ACCESS_CREDENTIAL_HASH_SECRET": "d" * 40,
        "NOTIFICATIONS_REDIS_URL": "redis://localhost:6379/0",
        "SANDBOX_TYPE": "e2b",
        "SANDBOX_REAPER_ENABLED": True,
        "CLOUD_AGENT_SANDBOX_PROVIDER": "fly",
        "CLOUD_AGENT_FLY_APP": "puppyone-ws",
        "CLOUD_AGENT_FLY_TOKEN": "tok-123",
        "CLOUD_AGENT_FLY_IMAGE": "registry.example/agent@sha256:" + "a" * 64,
        "CLOUD_AGENT_SPRITES_TOKEN": "spr-123",
        "CLOUD_AGENT_SPRITES_BUNDLE": "https://example.invalid/bundle.tar.gz",
        "CLOUD_AGENT_SPRITES_SHA256": "b" * 64,
    }
    # Default Tier 1 works cleanly on Fly.
    assert Settings(**base).CLOUD_AGENT_COLLAB_MODE == "shared_machine"

    # Tier 2 requires Fly provider in hosted environments.
    with pytest.raises(ValueError, match="fly_host_export"):
        Settings(
            **{
                **base,
                "CLOUD_AGENT_COLLAB_MODE": "fly_host_export",
                "CLOUD_AGENT_SANDBOX_PROVIDER": "sprites",
            }
        )
    assert (
        Settings(**{**base, "CLOUD_AGENT_COLLAB_MODE": "fly_host_export"}).CLOUD_AGENT_COLLAB_MODE
        == "fly_host_export"
    )

    # Tier 3 requires metadata schema prefix in hosted environments.
    with pytest.raises(ValueError, match="CLOUD_AGENT_JUICEFS_META_PREFIX"):
        Settings(**{**base, "CLOUD_AGENT_COLLAB_MODE": "juicefs_shared"})
    assert (
        Settings(
            **{
                **base,
                "CLOUD_AGENT_COLLAB_MODE": "juicefs_shared",
                "CLOUD_AGENT_JUICEFS_META_PREFIX": "staging",
            }
        ).CLOUD_AGENT_COLLAB_MODE
        == "juicefs_shared"
    )


@pytest.mark.asyncio
async def test_shared_machine_pre_agent_backup_checkpoints_dirty_default_workspace(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    row = {
        "id": "wb-1",
        "project_id": "proj-1",
        "is_default": True,
        "agent_registration_id": None,
        "state": "running",
        "unpublished_changes": True,
        "generation": "gen-1",
    }
    repo = SimpleNamespace(get=Mock(return_value=row), save_outcome=Mock())
    git = SimpleNamespace(snapshot=AsyncMock(return_value={"status": "saved", "tip": "a" * 40}))
    workspace = SimpleNamespace(repository=repo, git=git)

    result = await SharedMachineTopology().before_agent_session(
        workspace, {"workbench_id": "wb-1", "project_id": "proj-1", "access_grant_id": "g-1"}
    )
    assert result == {"status": "saved", "tip": "a" * 40}
    git.snapshot.assert_awaited_once_with(row, message=PRE_AGENT_BACKUP_MESSAGE)
    repo.save_outcome.assert_called_once_with(row, "saved", error=None, oid="a" * 40)

    # Clean workspace skips redundant snapshot.
    git.snapshot.reset_mock()
    row["unpublished_changes"] = False
    assert (
        await SharedMachineTopology().before_agent_session(
            workspace, {"workbench_id": "wb-1", "project_id": "proj-1"}
        )
        is None
    )
    git.snapshot.assert_not_awaited()

    # Transient snapshot failure logs and never blocks Agent admission.
    row["unpublished_changes"] = True
    git.snapshot.side_effect = RuntimeError("transient git error")
    assert (
        await SharedMachineTopology().before_agent_session(
            workspace, {"workbench_id": "wb-1", "project_id": "proj-1"}
        )
        is None
    )

    # Open repository policy disables automatic backup checkpoint.
    _set_policy(monkeypatch, "open")
    git.snapshot.reset_mock()
    assert (
        await SharedMachineTopology().before_agent_session(
            workspace, {"workbench_id": "wb-1", "project_id": "proj-1"}
        )
        is None
    )
    git.snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_fly_provider_propagates_pluggable_mount_env_without_overriding_security_env(
    monkeypatch,
):
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "fly-test-token")
    sent = []

    class FakeClient:
        async def request(self, method, path, **kwargs):
            sent.append((method, path, kwargs.get("json")))
            return SimpleNamespace(
                status_code=200,
                content=b'{"id":"mach-1"}',
                text='{"id":"mach-1"}',
                raise_for_status=lambda: None,
                json=lambda: {"id": "mach-1"},
            )

    provider = FlyRuntimeProvider(client=FakeClient())
    wb_id = str(uuid4())
    gen_id = str(uuid4())
    host = SimpleNamespace(
        artifact="registry.example/agent@sha256:" + "a" * 64,
        internet_access=True,
        sandbox_features=("desktop",),
        workspace_mount_env={
            "PUPPYONE_WORKSPACE_MOUNT_MODE": "fly_host_export",
            "PUPPYONE_HOST_EXPORT_PROTOCOL": "sshfs",
            "PUPPYONE_TASK_INTERNET": "ignored-override",
            "NON_PUPPYONE_VAR": "dropped",
        },
        resource={
            "app": workspace_app(wb_id),
            "workbench_id": wb_id,
            "workbench_generation": gen_id,
            "project_id": "proj-1",
        },
    )
    await provider._create_machine(host, {"id": "vol-1", "region": "sin"})
    post_payload = sent[0][2]
    env = post_payload["config"]["env"]
    assert env["PUPPYONE_TASK_INTERNET"] == "open"
    assert env["PUPPYONE_SANDBOX_FEATURES"] == "desktop"
    assert env["PUPPYONE_WORKSPACE_MOUNT_MODE"] == "fly_host_export"
    assert env["PUPPYONE_HOST_EXPORT_PROTOCOL"] == "sshfs"
    assert "NON_PUPPYONE_VAR" not in env


def test_public_sandbox_reports_isolated_agent_sandboxes_from_project_mode(monkeypatch):
    from src.platform.access.adapters.api.workbench_router import public_sandbox

    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")
    base_row = {"state": "running", "binding": {"provider": "fly", "profile": "base"}, "sandbox_settings": {}}
    assert public_sandbox(base_row)["settings"]["isolated_agent_sandboxes"] is False
    assert public_sandbox(base_row, collab_mode="fly_host_export")["settings"]["isolated_agent_sandboxes"] is True
    assert (
        public_sandbox({**base_row, "agent_collab_mode": "fly_host_export"})["settings"][
            "isolated_agent_sandboxes"
        ]
        is True
    )
    assert public_sandbox(base_row, collab_mode="shared_machine")["settings"]["isolated_agent_sandboxes"] is False
    # Global default takes effect when the project has no override.
    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "fly_host_export")
    assert public_sandbox(base_row)["settings"]["isolated_agent_sandboxes"] is True
    assert public_sandbox(base_row, collab_mode="shared_machine")["settings"]["isolated_agent_sandboxes"] is False


@pytest.mark.asyncio
async def test_set_sandbox_settings_isolated_agent_sandboxes_toggle_and_authorization(monkeypatch):
    from fastapi import HTTPException

    from src.platform.access.adapters.api.workbench_router import (
        SandboxSettings,
        public_sandbox,
        set_sandbox_settings as set_sandbox_route,
        status as status_route,
    )
    from src.platform.workspace.service import WorkbenchService
    from src.shared_types.grants import ProjectAction

    monkeypatch.setattr(settings, "CLOUD_AGENT_COLLAB_MODE", "shared_machine")
    row = {
        "id": str(uuid4()),
        "user_id": "admin-1",
        "project_id": "proj-1",
        "state": "running",
        "generation": str(uuid4()),
        "target_ref": "refs/heads/workspace",
        "is_default": True,
        "agent_registration_id": None,
        "owner_deleted_at": None,
        "binding": {"provider": "fly", "profile": "base", "artifact": "img"},
        "sandbox_settings": {},
    }
    state = {"collab_mode": None, "sandbox_calls": [], "collab_calls": []}

    repo = SimpleNamespace(
        context=lambda user, project: {
            "facts": {"user": user},
            "revision": {"v": 1},
            "workbench": row,
        },
        default=lambda user, project, rev: row,
        project_collab_mode=lambda project: state["collab_mode"],
        set_project_collab_mode=lambda project, mode: (
            state["collab_calls"].append((project, mode)),
            state.__setitem__("collab_mode", mode),
            mode,
        )[2],
        set_sandbox_settings=lambda r, value, rev, **kw: (
            state["sandbox_calls"].append(value),
            {**r, "sandbox_settings": value},
        )[1],
    )

    def authorize_facts(facts, project, user, action):
        if action == ProjectAction.PROJECT_MANAGE and user != "admin-1":
            raise HTTPException(status_code=403, detail="forbidden")
        return SimpleNamespace(org_id="org-1")

    service = WorkbenchService(
        transport=SimpleNamespace(),
        filesystem=SimpleNamespace(),
        billing=SimpleNamespace(),
        repository=repo,
        authorization=SimpleNamespace(authorize_facts=authorize_facts),
    )

    # Non-admin Editor cannot toggle project-wide isolated Agent sandboxes.
    with pytest.raises(HTTPException) as denied:
        await service.set_sandbox_settings("editor-1", "proj-1", {"isolated_agent_sandboxes": True})
    assert denied.value.status_code == 403
    assert state["collab_calls"] == []
    assert state["sandbox_calls"] == []

    # Invalid non-boolean value fails closed with 422.
    with pytest.raises(WorkspaceError) as bad:
        await service.set_sandbox_settings("admin-1", "proj-1", {"isolated_agent_sandboxes": "yes"})
    assert bad.value.status_code == 422

    # Admin enables Tier 2 via PUT /workbench/sandbox without mutating cloud_workbenches.sandbox_settings.
    resp = await set_sandbox_route(
        "proj-1",
        SandboxSettings(isolated_agent_sandboxes=True),
        user=SimpleNamespace(user_id="admin-1"),
        service=service,
    )
    assert resp["settings"]["isolated_agent_sandboxes"] is True
    assert state["collab_calls"] == [("proj-1", "fly_host_export")]
    assert state["sandbox_calls"] == []

    # GET /workbench reflects the persisted Tier 2 setting.
    service.opening = SimpleNamespace(status=AsyncMock(return_value=(row, None)))
    status_resp = await status_route(
        "proj-1",
        SimpleNamespace(headers={}),
        wait_seconds=0,
        user=SimpleNamespace(user_id="admin-1"),
        service=service,
    )
    assert status_resp["workspace"]["sandbox"]["settings"]["isolated_agent_sandboxes"] is True

    # Combined update writes both project collaboration mode and workbench sandbox_settings.
    combined = await service.set_sandbox_settings(
        "admin-1",
        "proj-1",
        {"desktop": True, "isolated_agent_sandboxes": False},
    )
    assert state["collab_calls"][-1] == ("proj-1", "shared_machine")
    assert state["sandbox_calls"] == [{"desktop": True}]
    assert public_sandbox(combined)["settings"] == {
        "desktop": True,
        "isolated_agent_sandboxes": False,
    }
