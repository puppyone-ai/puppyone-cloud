"""One link hands a Hosted Workspace to an Agent, with no account, CLI or renewal key.

The Agent generates an SSH key and applies with the link. A ``sign_in`` link gives
it a URL to pass to its person, and the Workspace owner allows it once; a ``link``
invitation admits it at once. From then on its SSH key is the only thing it keeps.
It runs commands over HTTPS, signing each one with that key, or over SSH where
its network allows. Grants, sessions, revocation and audit are the existing
lifecycle records.
"""

import asyncio
import logging
import re
from uuid import UUID

from fastapi import HTTPException

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.workspace.collaboration import get_agent_collaboration_router
from src.platform.workspace.errors import WorkspaceError

from .credential_repository import (
    access_token_hash,
    access_token_hash_candidates,
    generate_access_token,
)
from .credentials import normalize_ssh_public_key
from .grant_repository import AccessRepository
from .lifecycle_schemas import AGENT_AVATARS

AGENT_USER_PREFIX = "agent-"
SSH_KEY_PATH = "~/.ssh/puppyone_agent"
KNOWN_HOSTS_PATH = "~/.ssh/puppyone_known_hosts"
# One connection, kept open, carries every command: OpenSSH signs in once and
# runs each later command as a channel on it. The gateway's idle limit matches,
# and keepalives keep proxies from dropping it while a command is quiet.
CONNECTION_OPTIONS = (
    "-o ControlMaster=auto -o ControlPath=~/.ssh/puppyone-%C -o ControlPersist=30m "
    "-o ServerAliveInterval=15 -o ServerAliveCountMax=4"
)
POLL_INTERVAL_SECONDS = 3
INVITATION_PATTERN = re.compile(r"pai_[A-Za-z0-9_-]{43,128}")

RUN_PATH = "~/.puppyone/run"
RUN_CONNECTION_PATH = "~/.puppyone/connection"
# What an Agent saves once to run commands over HTTPS. It signs each command with
# the Agent's SSH key (``ssh-keygen -Y sign``) and sends it with curl, which uses
# the computer's HTTP proxy by itself; its exit status is the command's.
RUN_HELPER = r"""#!/bin/sh
# Runs one command in your PuppyOne workspace over HTTPS: ~/.puppyone/run 'pwd && ls'
[ $# -eq 1 ] || { echo "usage: $0 'command'" >&2; exit 2; }
{ read -r connection && read -r url; } < ~/.puppyone/connection ||
  { echo "PuppyOne: run https.install first." >&2; exit 2; }
dir=$(mktemp -d) || exit 1
trap 'rm -rf "$dir"' EXIT
printf '%s' "$1" > "$dir/command"
now=$(date +%s)
{ printf 'puppyone-agent-exec-v1\n%s\n%s\n' "$connection" "$now"; cat "$dir/command"; } > "$dir/message"
ssh-keygen -Y sign -f ~/.ssh/puppyone_agent -n puppyone-agent "$dir/message" 2> "$dir/error" ||
  { cat "$dir/error" >&2; exit 1; }
signature=$(sed '1d;$d' "$dir/message.sig" | tr -d '\n')
curl -sS -N -X POST "$url" -H "X-PuppyOne-Time: $now" -H "X-PuppyOne-Signature: $signature" \
  -H 'Content-Type: text/plain' --data-binary @"$dir/command" | tee "$dir/output"
status=$(tail -n 1 "$dir/output" | sed -n 's/^\[exit \([0-9]*\)\]$/\1/p')
exit "${status:-1}"
"""

logger = logging.getLogger(__name__)
# Machines being prepared for Agents just let in; held so the loop keeps them.
_PREPARING: set[asyncio.Task] = set()


def public_origin():
    origin = settings.PUBLIC_URL.rstrip("/")
    if not origin:
        raise HTTPException(503, detail={"code": "agent_link_not_configured"})
    return origin


AVATAR_CHOICES = ", ".join(AGENT_AVATARS)


def agent_instructions(invitation, *, mode=None):
    """The only thing an Agent reads, so it stays plain text it can run unchanged.

    A person pasted the link and asked the Agent to follow it. This page describes
    what is possible; it carries no authority until those steps finish.
    """
    api = public_origin() + "/api/v1/agent-links"
    topology = get_agent_collaboration_router().resolve(mode)
    return f"""# Connect to a PuppyOne hosted workspace

Someone shared this link so that you can work in their PuppyOne hosted workspace.
It needs `ssh-keygen` and `curl`, and nothing else to install.

## 1. Create a key

    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    [ -f {SSH_KEY_PATH} ] || ssh-keygen -q -t ed25519 -N '' -C puppyone-agent -f {SSH_KEY_PATH}

The private key stays on this computer. It is the only thing you need to keep.

## 2. Ask to connect

    curl -sS -X POST {api}/connect -H 'Content-Type: application/json' -d "{{\\"invitation\\":\\"{invitation}\\",\\"name\\":\\"YOUR AGENT NAME\\",\\"avatar\\":\\"YOUR AVATAR\\",\\"public_key\\":\\"$(cat {SSH_KEY_PATH}.pub)\\"}}"

Replace YOUR AGENT NAME with what your person calls you, such as Claude Code.
Replace YOUR AVATAR with the picture shown beside your name, choosing exactly one
of these, or other if none of them is you:
{AVATAR_CHOICES}

## 3. Follow the response

- `status` is `ready`: go to step 4 with its `https` object.
- `status` is `pending`: tell your person you are waiting for them to allow you in
  PuppyOne, and give them `sign_in_url` in case they are away from the app.
  Then repeat this every `poll_interval` seconds until `status` changes:

      curl -sS {api}/connections/CONNECTION_ID -H 'Authorization: Bearer POLL_SECRET'

  Use `connection_id` and `poll_secret` from the response. The sign-in link lasts
  10 minutes. `rejected`, `expired` or `revoked` means stop and tell your person.

## 4. Run commands

Run `https.install` once. Then save this helper once:

```
mkdir -p ~/.puppyone && cat > {RUN_PATH} <<'PUPPYONE_RUN' && chmod 700 {RUN_PATH}
{RUN_HELPER}PUPPYONE_RUN
```

Run each command with it:

    {RUN_PATH} 'pwd && ls'

It signs the command with your key and sends it over HTTPS, through this computer's
HTTP proxy if it has one. The output arrives as the command runs and ends with
`[exit N]`; the helper exits with the same status. Lines that start with
`PuppyOne:` come from PuppyOne, not from your command: while it prepares your
machine, and every 30 seconds a command prints nothing, to keep the connection open.
One command runs for at most 10 minutes, and nothing it starts keeps running after
it ends.

### Or over SSH

Where `ssh` can reach the internet, the `ssh` object in the response works too and
keeps a terminal open. Run `ssh.install_known_hosts` once, then use `ssh.command`,
adding what to run:

    SSH_COMMAND 'pwd && ls'

If `ssh` says `Connection closed` or `kex_exchange_identification`, this network only
lets HTTPS out: use {RUN_PATH} instead.

{topology.instructions_footer()}"""


def agent_connection(identity):
    """The connection behind a stable Agent SSH user; a one-use ticket has none."""
    if not isinstance(identity, str) or not identity.startswith(AGENT_USER_PREFIX):
        return None
    try:
        return str(UUID(identity.removeprefix(AGENT_USER_PREFIX)))
    except ValueError:
        return None


def https_access(connection):
    """How an Agent runs commands over HTTPS, in a form it can run unchanged."""
    url = f"{public_origin()}/api/v1/agent-links/connections/{connection}/exec"
    return {
        "url": url,
        "install": (
            f"mkdir -p ~/.puppyone && printf '%s\\n' '{connection}' '{url}' > {RUN_CONNECTION_PATH}"
        ),
        "command": RUN_PATH,
    }


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
            f"{CONNECTION_OPTIONS} -p {endpoint['port']} {user}@{endpoint['host']}"
        ),
    }


class AgentLinkRepository(AccessRepository):
    def agent_saves(self, project):
        """The last save of each Agent's Workspace (and the shared default), by branch."""
        rows = (
            self.client.table("cloud_workbenches")
            .select(
                "target_ref,save_status,save_error,save_finished_at,unpublished_changes,"
                "is_default,collaboration_mode"
            )
            .eq("project_id", project)
            .is_("owner_deleted_at", "null")
            .or_("agent_registration_id.not.is.null,is_default.eq.true")
            .execute()
            .data
        )
        saves = {}
        for row in rows or []:
            if row.get("collaboration_mode") == "fly_host_export" and not row.get("is_default"):
                continue
            saves[row["target_ref"]] = {
                "status": row["save_status"],
                "error": row["save_error"],
                "finished_at": row["save_finished_at"],
                "unsaved_work": row["unpublished_changes"],
            }
        return saves

    def project_collab_mode(self, project):
        """Return optional per-project collaboration mode override, if set."""
        rows = (
            self.client.table("projects")
            .select("agent_collab_mode")
            .eq("id", project)
            .limit(1)
            .execute()
            .data
        )
        return (rows[0] or {}).get("agent_collab_mode") if rows else None

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
    def __init__(self, repository=None, endpoint=None, workspaces=None, collaboration_router=None):
        self.repository = repository or AgentLinkRepository()
        self._endpoint = endpoint
        self._workspaces = workspaces
        self.collaboration = collaboration_router or get_agent_collaboration_router()

    def endpoint(self):
        if self._endpoint is not None:
            return self._endpoint()
        from .adapters.ssh.hosted import ssh_endpoint

        return ssh_endpoint()

    def _ready(self, connection, expires_at):
        return {
            "status": "ready",
            "expires_at": expires_at,
            "https": https_access(connection),
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
        saves = await run_blocking(self.repository.agent_saves, project)
        project_mode = None
        if callable(getattr(self.repository, "project_collab_mode", None)):
            try:
                project_mode = await run_blocking(self.repository.project_collab_mode, project)
            except Exception:
                project_mode = None
        connections = [
            {**connection, "save": saves.get(connection.get("branch"))}
            for connection in row.get("connections") or []
        ]
        return {
            **row,
            "connections": connections,
            "open_links_enabled": settings.AGENT_OPEN_LINKS_ENABLED,
            "collaboration_mode": self.collaboration.resolve(project_override=project_mode).mode,
        }

    async def stop(self, user, project):
        return await run_blocking(self.repository.command, "stop", user, project_id=project)

    async def review(self, user, connection):
        return await run_blocking(self.repository.command, "review", user, id=connection)

    async def decide(self, user, connection, decision):
        row = await run_blocking(
            self.repository.command, "decide", user, id=connection, decision=decision
        )
        if row.get("state") == "approved":
            self.prepare_soon(row["id"])
        return row

    def prepare_soon(self, connection):
        """Start the Agent's machine once it is let in, without making anyone wait for it."""
        task = asyncio.get_running_loop().create_task(self.prepare(connection))
        _PREPARING.add(task)
        task.add_done_callback(_PREPARING.discard)

    async def prepare(self, connection):
        """Ask for the Agent's machine now, so its first command does not wait for it.

        Only the intent is recorded; the sandbox module starts the machine. Best
        effort: the Agent's first connection prepares it anyway.
        """
        from src.platform.workspace.connections import RESUMABLE_STATES

        try:
            row = await self.workspace(connection)
            if row["state"] == "running" or row["state"] in RESUMABLE_STATES:
                return
            if self._workspaces is None:
                from .composition import build_workbench_service

                self._workspaces = build_workbench_service()
            actor = row.get("actor_user_id") or row["user_id"]
            if row.get("collaboration_mode") == "fly_host_export" and row.get("host_workbench_id"):
                host_row = await run_blocking(
                    self._workspaces.repository.get, row["host_workbench_id"]
                )
                if host_row and host_row["state"] != "running" and not host_row.get("open_requested"):
                    await self._workspaces.request_open(actor, row["project_id"], host_row)
            await self._workspaces.request_open(actor, row["project_id"], row)
        except Exception:
            logger.warning("agent_workspace_prepare_failed", exc_info=True)

    async def connect(self, request):
        try:
            key = normalize_ssh_public_key(request.public_key)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "access_invalid_request"}) from exc
        web = settings.FRONTEND_URL.rstrip("/")
        if not web:
            raise HTTPException(503, detail={"code": "agent_link_not_configured"})
        # Where its commands go: checked before anything is recorded.
        public_origin()
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
                agent_avatar=request.avatar,
                public_key=key,
            )
        except HTTPException as exc:
            if exc.status_code == 403:
                # Expired, revoked and unknown links are the same answer.
                raise HTTPException(403, detail={"code": "agent_link_unavailable"}) from exc
            raise
        if row["state"] == "approved":
            self.prepare_soon(row["id"])
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

    async def workspace(self, connection, *, mode=None):
        """The Workspace routed for this Agent by the active collaboration topology."""
        topology = self.collaboration.require_ready(mode)
        row = await run_blocking(
            self.repository.call,
            "access_agent_workspace",
            id=connection,
            mode=topology.sql_mode,
        )
        if isinstance(row, dict) and row.get("collaboration_mode"):
            self.collaboration.require_ready(project_override=row["collaboration_mode"])
        return row

    async def ticket(self, connection, *, mode=None):
        topology = self.collaboration.require_ready(mode)
        try:
            row = await run_blocking(
                self.repository.call,
                "access_agent_ssh_ticket",
                id=connection,
                mode=topology.sql_mode,
            )
        except HTTPException as exc:
            if exc.detail == {"code": "access_workspace_unavailable"}:
                raise WorkspaceError(409, {"code": "workbench_not_running"}) from exc
            raise
        return row["id"]
