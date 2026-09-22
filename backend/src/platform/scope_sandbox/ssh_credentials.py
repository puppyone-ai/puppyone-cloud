"""Per-user, short-lived, revocable SSH access to a scope sandbox (roadmap #5/#7).

Governance model (docs/proposals/PUP-sandbox-access-point.md):
  - A user proven to have scope permission gets THEIR public key added to the
    sandbox's authorized_keys, tagged with their PuppyOne user id and an OpenSSH
    ``expiry-time`` option so sshd natively rejects it after the TTL
    (short-lived). Revoke = remove that line (immediate) → "离职即失权".
  - Each user works in their OWN working tree (``~/<user_id>``) with their own
    git identity, so pushes attribute to the actual user even though all users
    share the sandbox's OS account (audit-to-person + correct collaboration
    attribution = roadmap #7).

Pure helpers (line build/parse) are unit-tested; grant/revoke/provision run via
``provider.exec`` (read-modify-write of authorized_keys, base64'd for safe
transport). All users share one OS account here — true per-OS-user isolation is
a heavier follow-up; for semi-trusted first-party devs the git-identity +
per-key attribution model is the practical boundary.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

from src.platform.scope_sandbox.scope_provision import (
    DEFAULT_GIT_CREDENTIAL_FILE,
    provision_scope_steps,
)
from src.platform.scope_sandbox.execution_policy import SSH_POLICY_WRAPPER_PATH

TAG = "puppyone"
AUTHORIZED_KEYS = "~/.ssh/authorized_keys"


# ── pure helpers ──────────────────────────────────────────────────────

def format_expiry(epoch: float) -> str:
    """OpenSSH ``expiry-time`` stamp, UTC-explicit (``YYYYMMDDHHMMSSZ``).

    The trailing ``Z`` makes sshd interpret the time as UTC instead of the box's
    local time zone (OpenSSH parses bare stamps in local time) — so the TTL is
    correct regardless of how the provider's image is configured. Supported by
    modern OpenSSH (verified live against the E2B/Fly sshd we run)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y%m%d%H%M%S") + "Z"


def user_comment(user_id: str) -> str:
    return f"{TAG}:user={user_id}"


def authorized_key_line(public_key: str, user_id: str, expiry: str) -> str:
    """A tagged, self-expiring authorized_keys line for one user."""
    pk = public_key.strip().replace("\n", " ")
    options = (
        f'expiry-time="{expiry}",no-agent-forwarding,no-X11-forwarding,'
        f'command="{SSH_POLICY_WRAPPER_PATH}"'
    )
    return f'{options} {pk} {user_comment(user_id)}'


def _line_user(line: str) -> str | None:
    """Return the user id a tagged line belongs to, else None."""
    tok = line.strip().split()
    if not tok:
        return None
    comment = tok[-1]
    prefix = f"{TAG}:user="
    return comment[len(prefix):] if comment.startswith(prefix) else None


def strip_user_lines(content: str, user_id: str) -> str:
    """Remove all lines tagged for ``user_id`` (keep everything else verbatim)."""
    kept = [ln for ln in content.splitlines() if _line_user(ln) != user_id]
    return ("\n".join(kept) + "\n") if kept else ""


def upsert_user_line(content: str, line: str, user_id: str) -> str:
    """Replace ``user_id``'s line(s) with ``line`` (one grant per user)."""
    base = strip_user_lines(content, user_id).rstrip("\n")
    return (f"{base}\n{line}\n" if base else f"{line}\n")


def granted_users(content: str) -> set[str]:
    out: set[str] = set()
    for ln in content.splitlines():
        u = _line_user(ln)
        if u:
            out.add(u)
    return out


# ── runtime (provider.exec) ───────────────────────────────────────────

async def _read_authorized_keys(provider, sandbox_id: str) -> str:
    out = await provider.exec(
        sandbox_id,
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh; "
        f"cat {AUTHORIZED_KEYS} 2>/dev/null | base64 | tr -d '\\n' || true",
    )
    b64 = (out.get("stdout") or "").strip()
    return base64.b64decode(b64).decode("utf-8", "replace") if b64 else ""


async def _write_authorized_keys(provider, sandbox_id: str, content: str) -> None:
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    await provider.exec(
        sandbox_id,
        f"printf %s '{b64}' | base64 -d > {AUTHORIZED_KEYS} && chmod 600 {AUTHORIZED_KEYS}",
    )


async def grant_ssh_access(
    provider,
    sandbox_id: str,
    user_id: str,
    public_key: str,
    *,
    expires_at: float,
) -> None:
    """Grant ``user_id`` SSH access until ``expires_at`` (epoch). Idempotent —
    replaces any prior grant for the same user."""
    expiry = format_expiry(expires_at)
    line = authorized_key_line(public_key, user_id, expiry)
    content = await _read_authorized_keys(provider, sandbox_id)
    await _write_authorized_keys(provider, sandbox_id, upsert_user_line(content, line, user_id))


async def revoke_ssh_access(provider, sandbox_id: str, user_id: str) -> None:
    """Immediately revoke ``user_id``'s SSH access (offboarding)."""
    content = await _read_authorized_keys(provider, sandbox_id)
    await _write_authorized_keys(provider, sandbox_id, strip_user_lines(content, user_id))


async def provision_user_workspace(
    provider,
    sandbox_id: str,
    user_id: str,
    *,
    git_url: str,
    user_email: str,
    user_name: str,
    credential_file: str = DEFAULT_GIT_CREDENTIAL_FILE,
) -> str:
    """Clone the scope into the user's OWN working tree (~/<user_id>) with their
    git identity (rebase-default). Returns the workdir. (roadmap #7)"""
    workdir = user_id
    for cmd in provision_scope_steps(
        git_url,
        workdir,
        user_email,
        user_name,
        credential_file,
    ):
        await provider.exec(sandbox_id, cmd)
    return workdir
