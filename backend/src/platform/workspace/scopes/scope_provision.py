"""Provision a scope's git workspace inside a sandbox (the cold-path bootstrap).

After a sandbox is CREATED, this makes it a ready working environment for the
scope: clone the scope's content via the PuppyOne git remote and configure git.
The Git locator is credential-free. A short-lived token is delivered through a
provider-sensitive file channel and consumed by a Git credential helper, so
neither the remote URL nor process arguments contain the secret. Warm sandbox
reuse rewrites that mode-0600 file on every connection.

Crucially it sets ``pull.rebase true``: PuppyOne enforces LINEAR history and
rejects merge-commit pushes ("merge commits are not supported; fetch and
rebase onto the remote main branch"), so the correct collaboration workflow is
rebase. Without this default, a user's plain ``git pull`` makes a merge commit
that the server rejects (see docs/proposals/sandbox-collab-session-results-2026-06.md).

Provider-agnostic: uses ``provider.exec`` (works for E2B/Fly). SSH provisioning
(ssh_e2b) is layered separately on top for E2B.
"""

from __future__ import annotations

DEFAULT_WORKDIR = "scope"
DEFAULT_GIT_CREDENTIAL_FILE = ".config/puppyone/git-http-token"


def _safe(value: str) -> str:
    # identity/url are server-controlled; strip quotes/newlines defensively so a
    # value can't break out of the shell command.
    return value.replace("'", "").replace('"', "").replace("\n", "").strip()


def provision_scope_steps(
    git_url: str,
    workdir: str,
    user_email: str,
    user_name: str,
    credential_file: str = DEFAULT_GIT_CREDENTIAL_FILE,
) -> list[str]:
    """Ordered shell commands to clone + configure the scope workspace. Pure."""
    wd = _safe(workdir).strip("/") or DEFAULT_WORKDIR
    url = _safe(git_url)
    email = _safe(user_email)
    name = _safe(user_name)
    credential_path = _safe(credential_file).lstrip("/")
    return [
        "command -v git >/dev/null 2>&1 || (sudo apt-get update -qq && sudo apt-get install -y -qq git)",
        "git config --global credential.useHttpPath true",
        (
            "git config --global credential.helper "
            f"'!f() {{ test \"$1\" = get || exit 0; "
            "echo username=x-puppyone-token; "
            "printf password=; "
            f"cat \"$HOME/{credential_path}\"; echo; }}; f'"
        ),
        # idempotent: re-running provisioning on sandbox reuse must not fail on an
        # existing clone (a user reconnecting hits this with the tree already there).
        f"test -d ~/{wd}/.git || GIT_TERMINAL_PROMPT=0 git clone {url} ~/{wd}",
        # PuppyOne rejects merge commits → rebase is the only valid pull workflow.
        f"git -C ~/{wd} config pull.rebase true",
        f"git -C ~/{wd} config user.email '{email}'",
        f"git -C ~/{wd} config user.name '{name}'",
    ]


async def provision_scope_workspace(
    provider,
    sandbox_id: str,
    *,
    git_url: str,
    workdir: str = DEFAULT_WORKDIR,
    user_email: str = "user@puppyone.ai",
    user_name: str = "puppyone",
    credential_file: str = DEFAULT_GIT_CREDENTIAL_FILE,
) -> None:
    """Run the clone + git-config steps inside ``sandbox_id`` via provider.exec.
    Raises if any step fails (a bare, unprovisioned workspace is unusable)."""
    for cmd in provision_scope_steps(
        git_url,
        workdir,
        user_email,
        user_name,
        credential_file,
    ):
        await provider.exec(sandbox_id, cmd)


async def write_scope_git_credential(
    provider,
    sandbox_id: str,
    credential: str,
    *,
    credential_file: str = DEFAULT_GIT_CREDENTIAL_FILE,
) -> None:
    """Renew the sandbox credential without putting it in a command or URL."""
    await provider.write_secret(sandbox_id, credential_file, credential)
