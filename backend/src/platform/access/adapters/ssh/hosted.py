"""Public-key delegation to one existing private Hosted Workspace."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncssh

from src.infra.execution.runner import run_blocking
from src.infra.supabase.dependencies import get_supabase_client
from src.platform.access.composition import build_workbench_service
from src.platform.authorization.models import ProjectAction
from src.platform.workspace.connections import WorkspaceConnections
from src.platform.workspace.errors import WorkspaceError

from .config import ssh_settings


def public_key(value):
    try:
        key = asyncssh.import_public_key(value)
        # Public keys only; never accept authorized_keys options or certificates.
        if key.get_algorithm() != "ssh-ed25519":
            raise ValueError("An Ed25519 public key is required")
        return key.export_public_key().decode().strip()
    except (ValueError, asyncssh.KeyImportError) as exc:
        raise WorkspaceError(422, {"code": "ssh_ed25519_public_key_required"}) from exc


class SshGrantRepository:
    def __init__(self, client=None):
        self.client = client if client is not None else get_supabase_client()

    def create(self, row):
        return self.client.table("workspace_ssh_grants").insert(row).execute().data[0]

    def get(self, identity):
        rows = (
            self.client.table("workspace_ssh_grants")
            .select("*")
            .eq("id", identity)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None

    def consume(self, identity):
        return self.client.rpc("workspace_ssh_grant_consume", {"p_id": identity}).execute().data

    def revoke(self, identity, user, project):
        return (
            self.client.table("workspace_ssh_grants")
            .update({"revoked_at": datetime.now(UTC).isoformat()})
            .eq("id", identity)
            .eq("user_id", user)
            .eq("project_id", project)
            .execute()
            .data
        )


class HostedSshAccess:
    def __init__(self, workspace, grants=None, connections=None, config=None):
        self.workspace = workspace
        self.grants = grants or SshGrantRepository()
        self.connections = connections or WorkspaceConnections(workspace)
        self.config = config or ssh_settings()

    def endpoint(self):
        import re

        config = self.config
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
            or datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
            <= datetime.now(UTC)
        ):
            raise PermissionError("SSH grant unavailable")
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
        async with self.connections.open(
            identity,
            grant["user_id"],
            grant["project_id"],
            grant["workbench_id"],
            grant["generation"],
        ) as connection:
            yield connection

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
