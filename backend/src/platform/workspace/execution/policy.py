"""Shared command-safety policy for every sandbox execution mode (ISSUE-009).

Both the sandbox HTTP endpoint (``connectors/sandbox_endpoint``) and the agent
bash tool (``connectors/agent``) route execution through
``SandboxService.exec``.  Enforcing the forbidden-pattern blacklist here — at
the single choke point — prevents per-caller policy drift, where one entry path
(the agent tool) historically skipped the validation the endpoint applied.

This is a defense-in-depth layer only. A regex blacklist is trivially evadable
(``$IFS``, encoding, variable splicing); the decisive isolation control is the
container boundary (see ISSUE-010: network isolation, cap-drop, no-new-privileges,
read-only rootfs).
"""

from __future__ import annotations

import base64
import re

# Patterns that must never run in the sandbox regardless of entry point:
# privilege escalation, host pseudo-filesystems, host lifecycle, and the cloud
# instance-metadata IP (SSRF / credential theft).
_FORBIDDEN_PATTERNS = [
    r"\bsudo\b",
    r"/etc(?:/|\b)",
    r"/proc(?:/|\b)",
    r"/sys(?:/|\b)",
    r"/dev(?:/|\b)",
    r"(^|\s)mount(\s|$)",
    r"(^|\s)umount(\s|$)",
    r"(^|\s)reboot(\s|$)",
    r"(^|\s)shutdown(\s|$)",
    r"(^|\s)mkfs(\s|$)",
    r"169\.254\.169\.254",
    r"\b2852039166\b",
    r"\b0x0*a9fe0*a9fe\b",
]
_COMPILED = [re.compile(pattern, re.IGNORECASE) for pattern in _FORBIDDEN_PATTERNS]
SSH_POLICY_WRAPPER_PATH = "/usr/local/bin/puppyone-ssh-policy"


class SandboxCommandRejected(ValueError):
    """Raised when a command matches a forbidden pattern."""


def assert_command_allowed(command: str) -> None:
    """Raise ``SandboxCommandRejected`` if the command hits the blacklist."""
    if not isinstance(command, str):
        return
    for pattern in _COMPILED:
        if pattern.search(command):
            raise SandboxCommandRejected("Command contains forbidden operations")


def ssh_policy_wrapper_install_command() -> str:
    """Return a root install command for an SSH forced-command wrapper.

    The generated wrapper embeds the exact same regular expressions as the HTTP
    and agent execution choke point, so policy changes cannot silently drift.
    Interactive shells are rejected because they would bypass per-command
    inspection; VSCode Remote-SSH uses an explicit original command.
    """
    script = f'''#!/usr/bin/env python3
import os
import re
import sys

patterns = {list(_FORBIDDEN_PATTERNS)!r}
command = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
if not command:
    print("Interactive SSH shells are disabled; use VSCode Remote-SSH or an explicit command.", file=sys.stderr)
    raise SystemExit(126)
if any(re.search(pattern, command, re.IGNORECASE) for pattern in patterns):
    print("Command rejected by PuppyOne sandbox policy.", file=sys.stderr)
    raise SystemExit(126)
os.execv("/bin/sh", ["sh", "-lc", command])
'''
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return (
        f"printf %s '{encoded}' | base64 -d | sudo tee {SSH_POLICY_WRAPPER_PATH} >/dev/null "
        f"&& sudo chown root:root {SSH_POLICY_WRAPPER_PATH} "
        f"&& sudo chmod 755 {SSH_POLICY_WRAPPER_PATH}"
    )
