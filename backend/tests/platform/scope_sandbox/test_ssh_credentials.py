"""Per-user short-lived/revocable SSH credential tests (roadmap #5/#7).

Pure helpers (line build/parse/strip/upsert) are tested directly; grant/revoke
runtime is tested against a fake provider that emulates the box's
authorized_keys file through exec (base64 read-modify-write)."""

from __future__ import annotations

import base64

from src.platform.scope_sandbox import ssh_credentials as sc

ED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc123 alice@laptop"


# ── pure helpers ──────────────────────────────────────────────────────

def test_format_expiry_is_utc_yyyymmddhhmmss():
    # 1780531200 == 2026-06-04T00:00:00Z; trailing Z makes sshd read it as UTC.
    assert sc.format_expiry(1780531200.0) == "20260604000000Z"


def test_authorized_key_line_has_expiry_option_and_user_tag():
    line = sc.authorized_key_line(ED, "alice", "20260607000000")
    assert line.startswith('expiry-time="20260607000000",no-agent-forwarding,')
    assert 'command="/usr/local/bin/puppyone-ssh-policy" ssh-ed25519 ' in line
    assert line.endswith(" puppyone:user=alice")


def test_authorized_key_line_flattens_multiline_key():
    line = sc.authorized_key_line("ssh-ed25519 AAAA\n", "bob", "20260101000000")
    assert "\n" not in line and line.endswith("puppyone:user=bob")


def test_granted_users_parses_only_tagged_lines():
    content = (
        "ssh-rsa AAAAuntagged me@host\n"  # manual key, no tag → ignored
        + sc.authorized_key_line(ED, "alice", "20260607000000") + "\n"
        + sc.authorized_key_line(ED, "bob", "20260607000000") + "\n"
    )
    assert sc.granted_users(content) == {"alice", "bob"}


def test_strip_user_lines_removes_only_that_user_keeps_others():
    content = (
        sc.authorized_key_line(ED, "alice", "20260607000000") + "\n"
        + "ssh-rsa AAAAuntagged keep@host\n"
        + sc.authorized_key_line(ED, "bob", "20260607000000") + "\n"
    )
    out = sc.strip_user_lines(content, "alice")
    assert sc.granted_users(out) == {"bob"}
    assert "keep@host" in out  # untagged manual key preserved verbatim


def test_strip_user_lines_substring_uid_not_falsely_matched():
    content = (
        sc.authorized_key_line(ED, "ali", "20260607000000") + "\n"
        + sc.authorized_key_line(ED, "alice", "20260607000000") + "\n"
    )
    out = sc.strip_user_lines(content, "ali")  # must NOT also strip "alice"
    assert sc.granted_users(out) == {"alice"}


def test_upsert_user_line_replaces_prior_grant_for_same_user():
    old = sc.authorized_key_line(ED, "alice", "20260101000000")
    new = sc.authorized_key_line(ED, "alice", "20270101000000")
    out = sc.upsert_user_line(old + "\n", new, "alice")
    assert out.count("puppyone:user=alice") == 1  # one grant per user
    assert "20270101000000" in out and "20260101000000" not in out


def test_strip_all_yields_empty():
    content = sc.authorized_key_line(ED, "alice", "20260607000000") + "\n"
    assert sc.strip_user_lines(content, "alice") == ""


# ── runtime over a fake provider (emulated authorized_keys file) ───────

class FakeBox:
    """Emulates a box: a single authorized_keys string mutated via exec()."""

    def __init__(self) -> None:
        self.authorized_keys = ""

    async def exec(self, sandbox_id: str, cmd: str) -> dict:
        if "base64 -d >" in cmd:  # write path: printf %s '<b64>' | base64 -d > file
            b64 = cmd.split("printf %s '", 1)[1].split("'", 1)[0]
            self.authorized_keys = base64.b64decode(b64).decode("utf-8")
            return {"stdout": "", "exit_code": 0}
        if "cat" in cmd:  # read path → returns base64 of current file
            enc = base64.b64encode(self.authorized_keys.encode("utf-8")).decode("ascii")
            return {"stdout": enc, "exit_code": 0}
        return {"stdout": "", "exit_code": 0}


async def test_grant_then_revoke_roundtrip():
    box = FakeBox()
    await sc.grant_ssh_access(box, "sb-1", "alice", ED, expires_at=1780531200.0)
    await sc.grant_ssh_access(box, "sb-1", "bob", ED, expires_at=1780531200.0)
    assert sc.granted_users(box.authorized_keys) == {"alice", "bob"}

    await sc.revoke_ssh_access(box, "sb-1", "alice")
    assert sc.granted_users(box.authorized_keys) == {"bob"}  # 离职即失权


async def test_grant_is_idempotent_per_user():
    box = FakeBox()
    await sc.grant_ssh_access(box, "sb-1", "alice", ED, expires_at=1780531200.0)
    await sc.grant_ssh_access(box, "sb-1", "alice", ED, expires_at=1800000000.0)
    assert box.authorized_keys.count("puppyone:user=alice") == 1  # renewed, not duplicated


async def test_provision_user_workspace_clones_into_user_dir():
    calls: list[str] = []

    class Box:
        async def exec(self, sandbox_id, cmd):
            calls.append(cmd)
            return {"stdout": "", "exit_code": 0}

    workdir = await sc.provision_user_workspace(
        Box(), "sb-1", "alice",
        git_url="https://qubits-api.puppyone.ai/git/project-1/scopes/scope-1.git",
        user_email="alice@corp.com", user_name="Alice",
    )
    assert workdir == "alice"
    joined = "\n".join(calls)
    assert "alice" in joined and "pull.rebase" in joined  # per-user tree + rebase default
    assert "alice@corp.com" in joined                     # per-user git identity
