"""Public-key delegation to one existing private Hosted Workspace."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from src.infra.execution.runner import run_blocking
from src.platform.access.composition import build_workbench_service
from src.platform.access.credential_repository import SshCredentialRepository
from src.platform.access.credentials import normalize_ssh_public_key
from src.platform.access.grants import GrantService
from src.platform.access.sessions import AccessSessions
from src.platform.authorization.models import ProjectAction
from src.platform.workspace.connections import WorkspaceConnections
from src.platform.workspace.errors import WorkspaceError

from .config import ssh_settings


def public_key(value):
    try:
        return normalize_ssh_public_key(value)
    except ValueError as exc:
        raise WorkspaceError(422, {"code": "ssh_ed25519_public_key_required"}) from exc


# Existing callers keep their import while persistence belongs to shared Access.
SshGrantRepository = SshCredentialRepository


def ssh_endpoint(config=None):
    import re

    config = config or ssh_settings()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", config.public_host):
        raise WorkspaceError(503, {"code": "workspace_ssh_not_configured"})
    try:
        key = public_key(config.public_host_key)
    except WorkspaceError as exc:
        raise WorkspaceError(503, {"code": "workspace_ssh_not_configured"}) from exc
    return {
        "host": config.public_host,
        "port": config.public_port,
        "host_public_key": key,
        "known_hosts": (
            f"{config.public_host} {key}"
            if config.public_port == 22
            else f"[{config.public_host}]:{config.public_port} {key}"
        ),
    }


class HostedSshAccess:
    def __init__(
        self, workspace, grants=None, connections=None, config=None, authority=None, sessions=None
    ):
        self.workspace = workspace
        self.grants = grants or SshGrantRepository()
        self.connections = connections or WorkspaceConnections(workspace)
        self.config = config or ssh_settings()
        self.authority = authority
        self.sessions = sessions

    def endpoint(self):
        return ssh_endpoint(self.config)

    def lifecycle(self):
        # Legacy tests/clients do not instantiate new storage until it is needed.
        if self.authority is None:
            self.authority = GrantService()
        if self.sessions is None:
            self.sessions = AccessSessions()
        return self.authority, self.sessions

    async def issue(self, user, project, generation, key, ttl):
        endpoint = self.endpoint()
        key = public_key(key)
        await run_blocking(
            self.workspace.context, user, project, ProjectAction.CREDENTIAL_MANAGE
        )
        row, _ = await self.workspace.bound(user, project, generation, write=True)
        await self.connections.authorize(user, project, row["id"], generation)
        await self.workspace.connection(row, capabilities={"workspace_shell"})
        if not 60 <= ttl <= 3600:
            raise WorkspaceError(422, {"code": "ssh_grant_invalid_expiry"})
        identity = str(uuid4())
        grant = await run_blocking(
            self.grants.create,
            {
                "id": identity,
                "user_id": user,
                "project_id": project,
                "workbench_id": row["id"],
                "generation": generation,
                "public_key": key,
                "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat(),
            },
        )
        return {
            **endpoint,
            "id": identity,
            "username": identity,
            "workbench_id": row["id"],
            "generation": generation,
            "expires_at": grant["expires_at"],
            "command": f"ssh -p {endpoint['port']} {identity}@{endpoint['host']}",
        }

    async def validate(self, identity, *, unused=False):
        grant = await run_blocking(self.grants.get, str(UUID(identity)))
        if (
            not grant
            or grant.get("revoked_at")
            or (unused and grant.get("used_at"))
            or (
                (unused or not grant.get("access_grant_id"))
                and datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
                <= datetime.now(UTC)
            )
        ):
            raise PermissionError("SSH grant unavailable")
        if grant.get("access_grant_id"):
            authority, _ = self.lifecycle()
            await authority.authorize(
                grant["access_grant_id"],
                "ssh",
                session=None if unused else identity,
                project=grant["project_id"],
                workbench=grant["workbench_id"],
            )
        await self.connections.authorize(
            grant["user_id"], grant["project_id"], grant["workbench_id"], grant["generation"]
        )
        return grant

    async def authenticate(self, identity, key):
        try:
            async with asyncio.timeout(10):
                grant = await self.validate(identity, unused=True)
                return public_key(key.export_public_key()) == grant["public_key"]
        except Exception:
            return False

    @asynccontextmanager
    async def open(self, identity):
        grant = await self.validate(identity, unused=True)
        await run_blocking(self.grants.consume, identity)
        try:
            async with self.connections.open(
                identity,
                grant["user_id"],
                grant["project_id"],
                grant["workbench_id"],
                grant["generation"],
            ) as connection:
                yield connection
        finally:
            if grant.get("access_grant_id"):
                _, sessions = self.lifecycle()
                await sessions.finish_unallocated(identity)

    async def renew(self, connection, shell):
        async with asyncio.timeout(10):
            await self.validate(connection["id"])
            await self.connections.renew(connection, shell)

    async def revoke(self, user, project, identity):
        await run_blocking(
            self.workspace.context, user, project, ProjectAction.CREDENTIAL_MANAGE
        )
        rows = await run_blocking(self.grants.revoke, identity, user, project)
        if not rows:
            raise WorkspaceError(404, {"code": "ssh_grant_not_found"})


def build_hosted_ssh():
    return HostedSshAccess(build_workbench_service())
