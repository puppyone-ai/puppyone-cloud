"""One link hands a Hosted Workspace to an Agent, with no account, CLI or renewal key.

The Agent generates an SSH key and applies with the link. A ``sign_in`` link gives
it a URL to pass to its person, and the Workspace owner allows it once; a ``link``
invitation admits it at once. From then on its SSH key is the only thing it keeps.
Grants, sessions, revocation and audit are the existing lifecycle records.
"""

import re
from uuid import UUID

from fastapi import HTTPException

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.workspace.errors import WorkspaceError

from .credential_repository import (
    access_token_hash,
    access_token_hash_candidates,
    generate_access_token,
)
from .credentials import normalize_ssh_public_key
from .grant_repository import AccessRepository

AGENT_USER_PREFIX = "agent-"
SSH_KEY_PATH = "~/.ssh/puppyone_agent"
KNOWN_HOSTS_PATH = "~/.ssh/puppyone_known_hosts"
POLL_INTERVAL_SECONDS = 3
INVITATION_PATTERN = re.compile(r"pai_[A-Za-z0-9_-]{43,128}")


def public_origin():
    origin = settings.PUBLIC_URL.rstrip("/")
    if not origin:
        raise HTTPException(503, detail={"code": "agent_link_not_configured"})
    return origin


def agent_instructions(invitation):
    """The only thing an Agent reads, so it stays plain text it can run unchanged.

    A person pasted the link and asked the Agent to follow it. This page describes
    what is possible; it carries no authority until those steps finish.
    """
    api = public_origin() + "/api/v1/agent-links"
    return f"""# Connect to a PuppyOne hosted workspace

Someone shared this link so that you can work in their PuppyOne hosted workspace
over SSH. It needs `ssh`, `ssh-keygen` and `curl`, and nothing else to install.

## 1. Create a key

    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    [ -f {SSH_KEY_PATH} ] || ssh-keygen -q -t ed25519 -N '' -C puppyone-agent -f {SSH_KEY_PATH}

The private key stays on this computer. It is the only thing you need to keep.

## 2. Ask to connect

    curl -sS -X POST {api}/connect -H 'Content-Type: application/json' -d "{{\\"invitation\\":\\"{invitation}\\",\\"name\\":\\"YOUR AGENT NAME\\",\\"public_key\\":\\"$(cat {SSH_KEY_PATH}.pub)\\"}}"

Replace YOUR AGENT NAME with what your person calls you, such as Claude Code.

## 3. Follow the response

- `status` is `ready`: go to step 4 with its `ssh` object.
- `status` is `pending`: send `sign_in_url` to your person and ask them to open it.
  Then repeat this every `poll_interval` seconds until `status` changes:

      curl -sS {api}/connections/CONNECTION_ID -H 'Authorization: Bearer POLL_SECRET'

  Use `connection_id` and `poll_secret` from the response. The sign-in link lasts
  10 minutes. `rejected`, `expired` or `revoked` means stop and tell your person.

## 4. Connect

Run `ssh.install_known_hosts` once. Then use `ssh.command`, adding what to run:

    SSH_COMMAND 'pwd && ls'

Each command starts in your own copy of the project, on a machine only you use.
Your files are saved to your own branch when the machine goes to sleep, and your
person decides when they join the project. Your first connection can take a few
minutes while the machine is prepared; after that, waking takes up to a minute.
"""


def agent_connection(identity):
    """The connection behind a stable Agent SSH user; a one-use ticket has none."""
    if not isinstance(identity, str) or not identity.startswith(AGENT_USER_PREFIX):
        return None
    try:
        return str(UUID(identity.removeprefix(AGENT_USER_PREFIX)))
    except ValueError:
        return None


def ssh_access(connection, endpoint):
    """Everything an Agent needs to connect, in a form it can run unchanged."""
    user = AGENT_USER_PREFIX + connection
    return {
        "host": endpoint["host"],
        "port": endpoint["port"],
        "user": user,
        "known_hosts": endpoint["known_hosts"],
        "install_known_hosts": f"printf '%s\\n' '{endpoint['known_hosts']}' > {KNOWN_HOSTS_PATH}",
        "command": (
            f"ssh -i {SSH_KEY_PATH} -o IdentitiesOnly=yes -o UserKnownHostsFile={KNOWN_HOSTS_PATH} "
            f"-p {endpoint['port']} {user}@{endpoint['host']}"
        ),
    }


class AgentLinkRepository(AccessRepository):
    def command(self, action, user, **payload):
        return self.call("access_agent_link_command", action=action, user=user, payload=payload)

    def admits_without_sign_in(self, key_hashes):
        rows = (
            self.client.table("access_invitations")
            .select("id")
            .in_("key_hash", key_hashes)
            .eq("admission", "link")
            .limit(1)
            .execute()
            .data
        )
        return bool(rows)


class AgentLinkService:
    def __init__(self, repository=None, endpoint=None):
        self.repository = repository or AgentLinkRepository()
        self._endpoint = endpoint

    def endpoint(self):
        if self._endpoint is not None:
            return self._endpoint()
        from .adapters.ssh.hosted import ssh_endpoint

        return ssh_endpoint()

    def _ready(self, connection, expires_at):
        return {
            "status": "ready",
            "expires_at": expires_at,
            "ssh": ssh_access(connection, self.endpoint()),
        }

    async def create(self, user, project, request):
        if request.admission == "link" and not settings.AGENT_OPEN_LINKS_ENABLED:
            raise HTTPException(409, detail={"code": "agent_open_links_closed"})
        self.endpoint()  # A link that cannot be connected to must not be issued.
        secret = request.secret.get_secret_value()
        origin = public_origin()
        row = await run_blocking(
            self.repository.command,
            "create",
            user,
            id=str(request.id),
            project_id=project,
            workbench_id=str(request.workbench_id),
            admission=request.admission,
            key_hash=access_token_hash(secret),
        )
        return {**row, "agent_url": f"{origin}/agent/{secret}"}

    async def inventory(self, user, project):
        row = await run_blocking(self.repository.command, "inventory", user, project_id=project)
        return {**row, "open_links_enabled": settings.AGENT_OPEN_LINKS_ENABLED}

    async def stop(self, user, project):
        return await run_blocking(self.repository.command, "stop", user, project_id=project)

    async def review(self, user, connection):
        return await run_blocking(self.repository.command, "review", user, id=connection)

    async def decide(self, user, connection, decision):
        return await run_blocking(
            self.repository.command, "decide", user, id=connection, decision=decision
        )

    async def connect(self, request):
        try:
            key = normalize_ssh_public_key(request.public_key)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "access_invalid_request"}) from exc
        web = settings.FRONTEND_URL.rstrip("/")
        if not web:
            raise HTTPException(503, detail={"code": "agent_link_not_configured"})
        self.endpoint()
        key_hashes = access_token_hash_candidates(request.invitation.get_secret_value())
        # Links issued before admission closed stay refused, in the same words
        # as an expired one: a link holder learns nothing about the switch.
        if not settings.AGENT_OPEN_LINKS_ENABLED and await run_blocking(
            self.repository.admits_without_sign_in, key_hashes
        ):
            raise HTTPException(403, detail={"code": "agent_link_unavailable"})
        poll_secret = generate_access_token("pac")
        try:
            row = await run_blocking(
                self.repository.call,
                "access_agent_connect",
                key_hashes=key_hashes,
                poll_hash=access_token_hash(poll_secret),
                agent_name=request.name,
                public_key=key,
            )
        except HTTPException as exc:
            if exc.status_code == 403:
                # Expired, revoked and unknown links are the same answer.
                raise HTTPException(403, detail={"code": "agent_link_unavailable"}) from exc
            raise
        if row["state"] == "approved":
            return self._ready(row["id"], None)
        return {
            "status": "pending",
            "sign_in_url": f"{web}/agent-connect/{row['id']}",
            "connection_id": row["id"],
            "poll_secret": poll_secret,
            "poll_interval": POLL_INTERVAL_SECONDS,
            "expires_at": row["expires_at"],
        }

    async def status(self, connection, secret):
        if not re.fullmatch(r"pac_[A-Za-z0-9_-]{43,128}", secret):
            raise HTTPException(401, detail={"code": "access_invalid_credential"})
        row = await run_blocking(
            self.repository.call,
            "access_agent_connection_status",
            id=connection,
            poll_hashes=access_token_hash_candidates(secret),
        )
        if row["state"] == "approved":
            return self._ready(row["id"], row["expires_at"])
        return {"status": row["state"], "poll_interval": POLL_INTERVAL_SECONDS}

    async def admits(self, connection, key):
        return await run_blocking(
            self.repository.call, "access_agent_ssh_admit", id=connection, public_key=key
        )

    async def workspace(self, connection):
        """The Agent's own machine; it may not have been started yet."""
        return await run_blocking(
            self.repository.call, "access_agent_workspace", id=connection
        )

    async def ticket(self, connection):
        try:
            row = await run_blocking(self.repository.call, "access_agent_ssh_ticket", id=connection)
        except HTTPException as exc:
            if exc.detail == {"code": "access_workspace_unavailable"}:
                raise WorkspaceError(409, {"code": "workbench_not_running"}) from exc
            raise
        return row["id"]
