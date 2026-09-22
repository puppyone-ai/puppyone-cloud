"""E2B SSH provisioning — sshd + websocat tunnel for VSCode Remote-SSH.

E2B has no native SSH / raw-TCP ingress; SSH is tunnelled over E2B's public
``wss`` proxy (every sandbox port ``N`` is reachable at ``N-<id>.e2b.app``).
Inside a sandbox this sets up:

  - sshd on :22 (host keys + the user's public key in authorized_keys)
  - a websocat server on ``forward_port`` bridging  wss → tcp:127.0.0.1:22

and the client connects with websocat as the SSH ``ProxyCommand``:

    ssh -o ProxyCommand="websocat --binary -B 65536 - wss://<port>-<id>.e2b.app" user@host

VSCode Remote-SSH uses exactly that ``ProxyCommand`` from ``~/.ssh/config``
(see :func:`vscode_ssh_config_block`).

Validated live end-to-end on 2026-06-07 against the default E2B template (Debian
13, non-root ``user`` with passwordless sudo, sshd preinstalled).

NOTE: runtime install (downloads websocat on each create) is fine for dev/
validation but SLOW for production — bake sshd + websocat into a custom E2B
template instead. The exec steps below are the spec for that template.
"""

from __future__ import annotations

from src.platform.scope_sandbox.execution_policy import (
    SSH_POLICY_WRAPPER_PATH,
    ssh_policy_wrapper_install_command,
)

# Pinned static musl build (matches the Windows client build used in tests).
WEBSOCAT_LINUX_URL = (
    "https://github.com/vi/websocat/releases/download/v1.13.0/"
    "websocat.x86_64-unknown-linux-musl"
)
DEFAULT_FORWARD_PORT = 8081
DEFAULT_SSH_PORT = 22
DEFAULT_USER = "user"
SSHD_CONFIG_PATH = "/etc/ssh/puppyone_sshd_config"  # root-owned dir (not world-writable)

# Hardened sshd config. The default E2B template's sshd accepts the SSH "none"
# auth method (PAM nullok / passwordless account) — i.e. it would let ANYONE in
# regardless of authorized_keys, which silently defeats the per-user credential
# governance (#5). We require publickey-only and disable every other method so a
# user's access is EXACTLY their authorized_keys line (grant = add, revoke =
# remove, expiry-time = TTL). {port} is filled at provision time.
SSHD_HARDENED_CONFIG = """\
Port {port}
HostKey /etc/ssh/ssh_host_ed25519_key
HostKey /etc/ssh/ssh_host_rsa_key
PidFile /run/sshd.pid
AuthorizedKeysFile .ssh/authorized_keys
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
PermitEmptyPasswords no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
UsePAM no
PermitRootLogin no
"""


def provision_steps(
    public_key: str | None = None, *, forward_port: int, ssh_port: int
) -> list[str]:
    """The ordered shell commands that turn a bare sandbox into an SSH target.

    Pure (no I/O) so it's unit-testable; the caller runs each via provider.exec.
    The last command starts the websocat forwarder detached (nohup &).

    ``public_key`` is OPTIONAL and, when given, becomes a permanent UNTAGGED
    authorized_keys entry that the per-user credential layer (ssh_credentials)
    cannot grant/revoke/expire. Production should leave it None and let
    ssh_credentials.grant_ssh_access own authorized_keys entirely (so every key
    is revocable + short-lived); pass a key only for a break-glass/admin bootstrap
    or a single-user demo. Either way ``~/.ssh/authorized_keys`` is created
    (mode 600) so sshd + later grants have a file to work with.
    """
    config = SSHD_HARDENED_CONFIG.format(port=ssh_port).replace("'", "")
    if public_key and public_key.strip():
        pk = public_key.strip().replace("'", "")  # authorized_keys is single-line; no quotes
        seed_authorized_keys = (
            f"printf '%s\\n' '{pk}' > ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
        )
    else:
        # No seed key: credential layer will populate it. Just ensure the file exists.
        seed_authorized_keys = "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    return [
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh",
        seed_authorized_keys,
        "sudo ssh-keygen -A && sudo mkdir -p /run/sshd",
        # publickey-only config (see SSHD_HARDENED_CONFIG): access == authorized_keys.
        f"printf '%s' '{config}' | sudo tee {SSHD_CONFIG_PATH} >/dev/null",
        ssh_policy_wrapper_install_command(),
        "command -v websocat >/dev/null 2>&1 || "
        f"(sudo curl -fsSL -o /usr/local/bin/websocat {WEBSOCAT_LINUX_URL} "
        "&& sudo chmod +x /usr/local/bin/websocat)",
        # Free :22 from the template's socket-activated default sshd (which allows
        # the "none" auth method) so OUR hardened, publickey-only sshd binds it.
        "sudo systemctl stop ssh.socket ssh.service 2>/dev/null; "
        "sudo pkill -x sshd 2>/dev/null; sleep 1; true",
        f"sudo mkdir -p /run/sshd && sudo /usr/sbin/sshd -f {SSHD_CONFIG_PATH}",
        f"nohup websocat --binary ws-l:0.0.0.0:{forward_port} "
        f"tcp:127.0.0.1:{ssh_port} >/tmp/websocat.log 2>&1 & echo forwarder-started",
    ]


def fast_provision_steps(
    public_key: str | None = None, *, forward_port: int = DEFAULT_FORWARD_PORT
) -> list[str]:
    """Provision steps for the CUSTOM template (roadmap #6), where sshd + websocat
    + the hardened config + host keys are already baked. Skips the download,
    keygen and config writes — only seeds authorized_keys (optional) and starts
    the pre-installed daemons via the baked ``puppyone-ssh-up`` helper."""
    steps = ["mkdir -p ~/.ssh && chmod 700 ~/.ssh"]
    if public_key and public_key.strip():
        pk = public_key.strip().replace("'", "")
        steps.append(
            f"printf '%s\\n' '{pk}' > ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
        )
    else:
        steps.append("touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys")
    steps.append(f"test -x {SSH_POLICY_WRAPPER_PATH}")
    steps.append(f"puppyone-ssh-up {forward_port}")
    return steps


async def provision_e2b_ssh(
    provider,
    sandbox_id: str,
    public_key: str | None = None,
    *,
    forward_port: int = DEFAULT_FORWARD_PORT,
    ssh_port: int = DEFAULT_SSH_PORT,
    baked: bool = False,
) -> None:
    """Run the SSH setup inside ``sandbox_id`` via the provider's exec.

    Sets up the hardened, publickey-only sshd + websocat tunnel. Leave
    ``public_key`` None in production and grant per-user keys via
    ssh_credentials (so all access is revocable + short-lived); pass a key only
    for an admin break-glass or single-user demo. Raises if any step fails.

    ``baked=True`` (custom template, roadmap #6) uses the FAST path — the heavy
    install is already in the image, so only seed + start are run.
    """
    steps = (
        fast_provision_steps(public_key, forward_port=forward_port)
        if baked
        else provision_steps(public_key, forward_port=forward_port, ssh_port=ssh_port)
    )
    for cmd in steps:
        await provider.exec(sandbox_id, cmd)


def ssh_proxy_command(websocat_path: str, wss_host: str) -> str:
    """The SSH ProxyCommand value (websocat over wss)."""
    return f"{websocat_path} --binary -B 65536 - wss://{wss_host}"


def vscode_ssh_config_block(
    *,
    host_alias: str,
    wss_host: str,
    key_path: str,
    websocat_path: str,
    user: str = DEFAULT_USER,
) -> str:
    """A ready-to-paste ``~/.ssh/config`` block for VSCode Remote-SSH."""
    return (
        f"Host {host_alias}\n"
        f"    HostName {wss_host}\n"
        f"    User {user}\n"
        f"    IdentityFile {key_path}\n"
        f"    ProxyCommand {ssh_proxy_command(websocat_path, wss_host)}\n"
        f"    StrictHostKeyChecking no\n"
        f"    UserKnownHostsFile /dev/null\n"
    )
