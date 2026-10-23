"""Unit tests for the E2B SSH provisioning helpers (pure parts)."""

from __future__ import annotations

from src.platform.workspace.scopes.ssh_e2b import (
    DEFAULT_FORWARD_PORT,
    DEFAULT_SSH_PORT,
    SSHD_CONFIG_PATH,
    fast_provision_steps,
    provision_e2b_ssh,
    provision_steps,
    ssh_proxy_command,
    vscode_ssh_config_block,
)

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY puppyone"


def test_provision_steps_order_and_content():
    steps = provision_steps(PUBKEY, forward_port=8081, ssh_port=22)
    blob = "\n".join(steps)
    assert "authorized_keys" in steps[1] and PUBKEY in steps[1]
    assert "ssh-keygen -A" in steps[2]
    assert "websocat" in blob                   # install (idempotent)
    # sshd must start from our hardened config, not the default (no "none" auth)
    sshd = next(s for s in steps if "/usr/sbin/sshd" in s)
    assert f"-f {SSHD_CONFIG_PATH}" in sshd and "-p 22" not in sshd
    # forwarder is last and detached, bridging the forward port → sshd
    assert "ws-l:0.0.0.0:8081" in steps[-1] and "tcp:127.0.0.1:22" in steps[-1]
    assert "nohup" in steps[-1] and steps[-1].rstrip().endswith("forwarder-started")


def test_provision_steps_without_key_seeds_empty_authorized_keys():
    # Production path: no seed key — credential layer owns authorized_keys, so
    # every key is revocable/short-lived. The file is still created for sshd.
    steps = provision_steps(forward_port=8081, ssh_port=22)
    assert any("touch ~/.ssh/authorized_keys" in s for s in steps)
    # no public key got written as a permanent untagged line
    assert not any("printf '%s\\n'" in s and "ssh-" in s for s in steps)


def test_provision_steps_with_key_still_seeds_it():
    steps = provision_steps(PUBKEY, forward_port=8081, ssh_port=22)
    assert any(PUBKEY in s and "authorized_keys" in s for s in steps)


def test_provision_steps_hardens_sshd_to_publickey_only():
    steps = provision_steps(PUBKEY, forward_port=8081, ssh_port=22)
    config_step = next(s for s in steps if SSHD_CONFIG_PATH in s and "sshd" not in s.split()[0])
    # the written config must forbid every non-key auth path that "none" rides on
    for directive in (
        "AuthenticationMethods publickey",
        "PasswordAuthentication no",
        "PermitEmptyPasswords no",
        "UsePAM no",
    ):
        assert directive in config_step


def test_provision_steps_sanitizes_quotes_in_key():
    # A key with an embedded quote+command must not break out of the single
    # quoting. After sanitization the step has exactly 4 single quotes
    # (the two pairs: '%s\n' and '<key>') — a stray quote would make 5+.
    steps = provision_steps("ssh-ed25519 ABC'; rm -rf / #", forward_port=1, ssh_port=2)
    assert steps[1].count("'") == 4


def test_fast_provision_steps_skip_install_and_start_baked_daemons():
    # custom-template path (roadmap #6): no download / keygen / config — only seed
    # the key and start the pre-baked daemons via puppyone-ssh-up.
    steps = fast_provision_steps(PUBKEY, forward_port=8081)
    blob = "\n".join(steps)
    assert "websocat" not in blob and "ssh-keygen" not in blob and "/usr/sbin/sshd" not in blob
    assert PUBKEY in steps[1] and "authorized_keys" in steps[1]
    assert steps[-1] == "puppyone-ssh-up 8081"


def test_fast_provision_without_key_just_touches_authorized_keys():
    steps = fast_provision_steps(forward_port=9000)
    assert any("touch ~/.ssh/authorized_keys" in s for s in steps)
    assert steps[-1] == "puppyone-ssh-up 9000"


async def test_provision_e2b_ssh_baked_uses_fast_path():
    calls: list[str] = []

    class FakeProvider:
        async def exec(self, sandbox_id, command):
            calls.append(command)
            return {"exit_code": 0}

    await provision_e2b_ssh(FakeProvider(), "sb-1", baked=True)
    blob = "\n".join(calls)
    assert "puppyone-ssh-up" in blob
    assert "websocat" not in blob and "ssh-keygen" not in blob   # nothing installed at runtime


def test_ssh_proxy_command():
    cmd = ssh_proxy_command(r"C:\bin\websocat.exe", "8081-abc.e2b.app")
    assert cmd == r"C:\bin\websocat.exe --binary -B 65536 - wss://8081-abc.e2b.app"


def test_vscode_config_block():
    block = vscode_ssh_config_block(
        host_alias="puppy-scope-1",
        wss_host="8081-abc.e2b.app",
        key_path=r"C:\keys\id_ed25519",
        websocat_path=r"C:\bin\websocat.exe",
    )
    assert "Host puppy-scope-1" in block
    assert "HostName 8081-abc.e2b.app" in block
    assert "ProxyCommand C:\\bin\\websocat.exe --binary -B 65536 - wss://8081-abc.e2b.app" in block
    assert "User user" in block
    assert "StrictHostKeyChecking no" in block


def test_defaults():
    assert DEFAULT_FORWARD_PORT == 8081 and DEFAULT_SSH_PORT == 22
