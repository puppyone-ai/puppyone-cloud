"""Pluggable multi-agent workspace collaboration topology router.

Decouples Access, SSH/HTTPS gateways, Desktop live files, and Git backup from
how external Agents share a Project's working files on Fly.io:

- ``shared_machine`` (Tier 1, ready):
    Invited external Agents route directly into the Project's single shared
    default Workspace VM. Edits land directly on local ext4 NVMe and trigger
    Desktop's live file watcher immediately with zero merge step.
- ``fly_host_export`` (Tier 2, pluggable slot):
    Each external Agent runs in its own isolated Fly MicroVM and mounts
    ``/workspace/repo`` over Fly's 6pn private network from the Project default
    Workspace machine (SSHFS or user-space NFS-Ganesha).
- ``juicefs_shared`` (Tier 3, pluggable slot):
    Each external Agent runs in its own isolated Fly MicroVM and mounts a
    per-Project JuiceFS POSIX volume (isolated Postgres schema + prefix S3).
- ``isolated_branch`` (Legacy / rollback, ready):
    Each external Agent runs in its own VM on ``refs/heads/agents/<registration>``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.repository_policy import get_repository_policy

from .errors import WorkspaceError

logger = logging.getLogger(__name__)

AgentCollaborationMode = Literal[
    "shared_machine",
    "fly_host_export",
    "juicefs_shared",
    "isolated_branch",
]

VALID_COLLABORATION_MODES: frozenset[str] = frozenset(
    {"shared_machine", "fly_host_export", "juicefs_shared", "isolated_branch"}
)

PRE_AGENT_BACKUP_MESSAGE = "Backup before external Agent session"
PRE_AGENT_BACKUP_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class WorkspaceMountSpec:
    """Declarative mount configuration handed to the worker boot layer (``fly-init.mjs``)."""

    mode: Literal["local", "fly_host_export", "juicefs_shared"]
    target_path: str = "/workspace/repo"
    protocol: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def worker_env(self) -> dict[str, str]:
        """Environment variables passed to the Fly Machine init process."""
        if self.mode == "local":
            return {}
        return {"PUPPYONE_WORKSPACE_MOUNT_MODE": self.mode, **self.env}


@runtime_checkable
class AgentCollaborationTopology(Protocol):
    """Contract every pluggable multi-agent collaboration strategy implements."""

    mode: AgentCollaborationMode
    tier: int
    implemented: bool
    shared_live_files: bool
    isolated_compute_vm: bool
    uses_agent_branch: bool
    mount_mode: Literal["local", "fly_host_export", "juicefs_shared"]

    @property
    def sql_mode(self) -> AgentCollaborationMode: ...

    def describe(self) -> dict[str, Any]: ...

    def instructions_footer(self) -> str: ...

    def resolve_mount(
        self,
        compute_row: dict[str, Any],
        storage_row: dict[str, Any] | None = None,
    ) -> WorkspaceMountSpec: ...

    async def before_agent_session(
        self,
        workspace_service: Any,
        grant: dict[str, Any],
    ) -> dict[str, Any] | None: ...


class _BaseTopology:
    mode: AgentCollaborationMode
    tier: int
    implemented: bool
    shared_live_files: bool
    isolated_compute_vm: bool
    uses_agent_branch: bool
    mount_mode: Literal["local", "fly_host_export", "juicefs_shared"]

    @property
    def sql_mode(self) -> AgentCollaborationMode:
        return self.mode

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tier": self.tier,
            "implemented": self.implemented,
            "shared_live_files": self.shared_live_files,
            "isolated_compute_vm": self.isolated_compute_vm,
            "uses_agent_branch": self.uses_agent_branch,
            "mount_mode": self.mount_mode,
        }

    def resolve_mount(
        self,
        compute_row: dict[str, Any],
        storage_row: dict[str, Any] | None = None,
    ) -> WorkspaceMountSpec:
        return WorkspaceMountSpec(mode="local")

    async def before_agent_session(
        self,
        workspace_service: Any,
        grant: dict[str, Any],
    ) -> dict[str, Any] | None:
        return None


async def _checkpoint_default_workspace(
    workspace_service: Any,
    workbench_id: str | None,
    grant: dict[str, Any],
) -> dict[str, Any] | None:
    """Best-effort safety backup of a Project's shared default Workspace before Agent edits."""
    if not workbench_id or not get_repository_policy().backup.on_pause:
        return None
    repo = getattr(workspace_service, "repository", None)
    git = getattr(workspace_service, "git", None)
    if repo is None or git is None or not callable(getattr(repo, "get", None)):
        return None
    snapshot = getattr(git, "snapshot", None)
    if not callable(snapshot):
        return None
    try:
        row = await run_blocking(repo.get, workbench_id)
        if not isinstance(row, dict):
            return None
        if (
            not row.get("is_default")
            or row.get("agent_registration_id")
            or row.get("state") != "running"
            or not row.get("unpublished_changes")
        ):
            return None
        async with asyncio.timeout(PRE_AGENT_BACKUP_TIMEOUT_SECONDS):
            result = await snapshot(row, message=PRE_AGENT_BACKUP_MESSAGE)
        if isinstance(result, dict) and result.get("status") == "saved":
            save_outcome = getattr(repo, "save_outcome", None)
            if callable(save_outcome):
                await run_blocking(
                    save_outcome,
                    row,
                    "saved",
                    error=None,
                    oid=result.get("tip"),
                )
        return result
    except Exception:
        logger.warning(
            "shared_machine_pre_agent_backup_skipped",
            extra={
                "workbench_id": workbench_id,
                "project_id": grant.get("project_id"),
                "access_grant_id": grant.get("access_grant_id"),
            },
            exc_info=True,
        )
        return None


class SharedMachineTopology(_BaseTopology):
    """Tier 1: External Agents execute inside the Project's shared default Workspace VM."""

    mode: AgentCollaborationMode = "shared_machine"
    tier = 1
    implemented = True
    shared_live_files = True
    isolated_compute_vm = False
    uses_agent_branch = False
    mount_mode: Literal["local"] = "local"

    def instructions_footer(self) -> str:
        return (
            "Each command runs directly in this project's shared workspace, so file edits appear\n"
            "immediately for your person and other collaborators in PuppyOne. Your home directory\n"
            "is kept from one command to the next, so settings such as `git config --global` stay.\n"
            "This workspace stays on its branch, so work there; `git switch`, `git rebase` and\n"
            "`git worktree add` are refused. Changes are backed up to the project repository\n"
            "automatically. Your first connection can take a few minutes while the machine is\n"
            "prepared, and says so while it waits; after that, waking takes up to a minute.\n"
        )

    async def before_agent_session(
        self,
        workspace_service: Any,
        grant: dict[str, Any],
    ) -> dict[str, Any] | None:
        return await _checkpoint_default_workspace(
            workspace_service, grant.get("workbench_id"), grant
        )


class FlyHostExportTopology(_BaseTopology):
    """Tier 2: Isolated Fly MicroVMs mounting /workspace/repo over Fly 6pn.

    The Project's default Workspace Fly Machine owns the real ext4 NVMe disk and
    exports ``/workspace/repo`` via SSH-tunneled kernel NFSv4.1 (NFS-Ganesha bound
    strictly to 127.0.0.1:2049 behind per-Agent Ed25519 SSH tunnels on Fly 6pn) or
    optional SSHFS.
    """

    mode: AgentCollaborationMode = "fly_host_export"
    tier = 2
    implemented = True
    shared_live_files = True
    isolated_compute_vm = True
    uses_agent_branch = False
    mount_mode: Literal["fly_host_export"] = "fly_host_export"

    def instructions_footer(self) -> str:
        return (
            "Each command runs in your own isolated machine mounted onto this project's shared\n"
            "workspace files, so edits appear immediately for your person in PuppyOne. Your home\n"
            "directory is kept from one command to the next. Changes are backed up to the project\n"
            "repository automatically. Your first connection can take a few minutes while the\n"
            "machine is prepared, and says so while it waits; after that, waking takes up to a minute.\n"
        )

    async def before_agent_session(
        self,
        workspace_service: Any,
        grant: dict[str, Any],
    ) -> dict[str, Any] | None:
        repo = getattr(workspace_service, "repository", None)
        if repo is None or not callable(getattr(repo, "get", None)):
            return None
        try:
            agent_row = await run_blocking(repo.get, grant.get("workbench_id"))
        except Exception:
            return None
        if not isinstance(agent_row, dict) or not agent_row.get("host_workbench_id"):
            return None
        return await _checkpoint_default_workspace(
            workspace_service, agent_row["host_workbench_id"], grant
        )

    def resolve_mount(
        self,
        compute_row: dict[str, Any],
        storage_row: dict[str, Any] | None = None,
    ) -> WorkspaceMountSpec:
        if not storage_row:
            raise WorkspaceError(
                503,
                {"code": "agent_collaboration_host_missing", "mode": self.mode},
            )
        resource = storage_row.get("resource") or {}
        host_app = resource.get("app") or settings.CLOUD_AGENT_FLY_APP
        host_machine = resource.get("resource_id") or ""
        protocol = settings.CLOUD_AGENT_FLY_HOST_EXPORT_PROTOCOL
        target = f"{host_machine}.vm.{host_app}.internal" if host_machine and host_app else ""
        return WorkspaceMountSpec(
            mode="fly_host_export",
            target_path="/workspace/repo",
            protocol=protocol,
            env={
                "PUPPYONE_HOST_EXPORT_PROTOCOL": protocol,
                "PUPPYONE_HOST_EXPORT_TARGET": target,
            },
            metadata={
                "host_workbench_id": storage_row["id"],
                "host_generation": storage_row.get("generation"),
                "host_machine_id": host_machine,
                "host_app": host_app,
            },
        )


def _juicefs_schema_name(project_id: str, prefix: str = "") -> str:
    safe = re.sub(r"[^a-z0-9_]", "_", project_id.lower()).strip("_") or "project"
    base = f"{prefix.strip('_')}_" if prefix.strip("_") else ""
    return f"jfs_{base}{safe}"[:63]


class JuiceFsSharedTopology(_BaseTopology):
    """Tier 3 (pluggable slot): Isolated Fly MicroVMs sharing a per-Project JuiceFS volume."""

    mode: AgentCollaborationMode = "juicefs_shared"
    tier = 3
    implemented = False
    shared_live_files = True
    isolated_compute_vm = True
    uses_agent_branch = False
    mount_mode: Literal["juicefs_shared"] = "juicefs_shared"

    def instructions_footer(self) -> str:
        return (
            "Each command runs in your own isolated machine mounted onto this project's shared\n"
            "workspace storage, so edits appear immediately for your person in PuppyOne. Your home\n"
            "directory is kept from one command to the next. Changes are backed up to the project\n"
            "repository automatically. Your first connection can take a few minutes while the\n"
            "machine is prepared, and says so while it waits; after that, waking takes up to a minute.\n"
        )

    def resolve_mount(
        self,
        compute_row: dict[str, Any],
        storage_row: dict[str, Any] | None = None,
    ) -> WorkspaceMountSpec:
        project_id = str(compute_row["project_id"])
        schema = _juicefs_schema_name(project_id, settings.CLOUD_AGENT_JUICEFS_META_PREFIX)
        return WorkspaceMountSpec(
            mode="juicefs_shared",
            target_path="/workspace/repo",
            protocol="juicefs",
            env={
                "PUPPYONE_JUICEFS_SCHEMA": schema,
                # Credentials are injected via root-only runtime files, never plain task env.
                "PUPPYONE_JUICEFS_META_URL_FILE": "/run/puppyone-workbench/juicefs-meta.url",
            },
            metadata={
                "project_id": project_id,
                "schema": schema,
            },
        )


class IsolatedBranchTopology(_BaseTopology):
    """Legacy / rollback mode: each external Agent owns a dedicated VM and Git branch."""

    mode: AgentCollaborationMode = "isolated_branch"
    tier = 0
    implemented = True
    shared_live_files = False
    isolated_compute_vm = True
    uses_agent_branch = True
    mount_mode: Literal["local"] = "local"

    def instructions_footer(self) -> str:
        return (
            "Each command starts in your own copy of the project, on a machine only you use.\n"
            "Your home directory is kept from one command to the next, so settings such as\n"
            "`git config --global` stay. This machine stays on one branch, so commit there;\n"
            "`git switch`, `git rebase` and `git worktree add` are refused. Your files are saved\n"
            "to that branch when the machine goes to sleep, and your person decides when they\n"
            "join the project. Your first connection can take a few minutes while the machine is\n"
            "prepared, and says so while it waits; after that, waking takes up to a minute.\n"
        )


class AgentCollaborationRouter:
    """Registry and runtime selector for pluggable multi-agent workspace topologies."""

    def __init__(self, topologies: tuple[AgentCollaborationTopology, ...] | None = None):
        self._registry: dict[str, AgentCollaborationTopology] = {}
        defaults: tuple[AgentCollaborationTopology, ...] = topologies or (
            SharedMachineTopology(),
            FlyHostExportTopology(),
            JuiceFsSharedTopology(),
            IsolatedBranchTopology(),
        )
        for strategy in defaults:
            self.register(strategy)

    def register(self, strategy: AgentCollaborationTopology) -> None:
        if strategy.mode not in VALID_COLLABORATION_MODES:
            raise ValueError(f"Unsupported collaboration mode: {strategy.mode!r}")
        self._registry[strategy.mode] = strategy

    def resolve(
        self,
        mode: str | None = None,
        *,
        project_override: str | None = None,
    ) -> AgentCollaborationTopology:
        selected = project_override or mode or settings.CLOUD_AGENT_COLLAB_MODE
        strategy = self._registry.get(selected)
        if strategy is None:
            raise WorkspaceError(
                422,
                {"code": "agent_collaboration_mode_invalid", "mode": str(selected)},
            )
        return strategy

    def require_ready(
        self,
        mode: str | None = None,
        *,
        project_override: str | None = None,
    ) -> AgentCollaborationTopology:
        strategy = self.resolve(mode, project_override=project_override)
        if not strategy.implemented:
            raise WorkspaceError(
                503,
                {"code": "agent_collaboration_mode_not_ready", "mode": strategy.mode},
            )
        return strategy

    def list_modes(self) -> list[dict[str, Any]]:
        return [
            self._registry[name].describe()
            for name in ("shared_machine", "fly_host_export", "juicefs_shared", "isolated_branch")
            if name in self._registry
        ]


_DEFAULT_ROUTER = AgentCollaborationRouter()


def get_agent_collaboration_router() -> AgentCollaborationRouter:
    """Return the process-wide pluggable collaboration router."""
    return _DEFAULT_ROUTER
