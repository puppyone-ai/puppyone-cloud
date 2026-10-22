"""SSH access to preserved Scope workspaces; resource ownership stays in Workspace.

This adapter preserves /scope-sandboxes. It does not expose a new public SSH
listener or attach a Scope credential to a Project's hosted workbench.
"""

from dataclasses import asdict, dataclass

from src.platform.access.adapters.git.locator import canonical_git_url
from src.platform.access.surface_repository import AccessSurfaceRepository
from src.platform.workspace.scopes.service import ScopeSandboxService, WorkspaceConnection


@dataclass
class ConnectInfo(WorkspaceConnection):
    needs_websocat: bool
    ssh_config_block: str


class SshAccessService:
    def __init__(self, workspace: ScopeSandboxService):
        self.workspace = workspace

    async def connect(self, **values) -> ConnectInfo:
        connection = await self.workspace.connect(**values)
        return ConnectInfo(**self._present(values["scope_id"], asdict(connection)))

    def status(self, **values) -> dict:
        return self._present(values["scope_id"], self.workspace.status(**values))

    async def revoke(self, **values) -> int:
        return await self.workspace.revoke(**values)

    def available_providers(self) -> dict:
        return self.workspace.available_providers()

    @staticmethod
    def _present(scope_id, connection):
        if "host" not in connection:
            return connection
        proxy = connection.get("proxy_command")
        return {
            **connection,
            "needs_websocat": proxy is not None and "websocat" in proxy,
            "ssh_config_block": _ssh_config_block(
                scope_id,
                connection["host"],
                connection["port"],
                connection["username"],
                proxy,
            ),
        }


def _ssh_config_block(
    scope_id: str,
    host: str,
    port: int,
    username: str,
    proxy_command: str | None,
) -> str:
    """A ready-to-paste ``~/.ssh/config`` block (provider-agnostic)."""
    alias = f"puppy-{scope_id[:8]}"
    lines = [
        f"Host {alias}",
        f"    HostName {host}",
        f"    User {username}",
        f"    Port {port}",
        "    IdentityFile ~/.ssh/id_ed25519   # path to YOUR private key",
    ]
    if proxy_command:
        lines.append(f"    ProxyCommand {proxy_command}")
    lines += [
        "    StrictHostKeyChecking no",
        "    UserKnownHostsFile /dev/null",
    ]
    return "\n".join(lines) + "\n"


_SERVICE: SshAccessService | None = None


def _issue_scope_credential(scope, user, expires):
    return AccessSurfaceRepository().issue_scope_session_credential(
        scope_id=scope,
        created_by=user,
        expires_at=expires,
    )


def _issue_git_credential(scope, user, expires):
    return AccessSurfaceRepository().issue_git_session_credential(
        scope_id=scope,
        created_by=user,
        expires_at=expires,
    )


def get_ssh_access_service() -> SshAccessService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = SshAccessService(
            ScopeSandboxService(
                scope_credential_issuer=_issue_scope_credential,
                git_credential_issuer=_issue_git_credential,
                git_locator=canonical_git_url,
            )
        )
    return _SERVICE
