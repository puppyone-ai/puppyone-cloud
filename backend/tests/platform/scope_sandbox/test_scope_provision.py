"""Tests for scope workspace provisioning (clone + rebase-default git config)."""

from __future__ import annotations

from src.platform.scope_sandbox.scope_provision import (
    DEFAULT_WORKDIR,
    provision_scope_steps,
)

URL = "https://qubits-api.puppyone.ai/git/project-1/scopes/scope-1.git"


def test_steps_clone_and_set_rebase_default():
    steps = provision_scope_steps(URL, "scope", "u@p.ai", "puppy")
    joined = "\n".join(steps)
    assert "git clone" in joined and URL in joined
    # the decisive line: PuppyOne rejects merge commits → rebase must be default
    assert any("config pull.rebase true" in s for s in steps)
    assert any("config user.email" in s for s in steps)
    assert any("config user.name" in s for s in steps)
    assert "x-puppyone-token" in joined
    assert "$HOME/.config/puppyone/git-http-token" in joined
    assert "password=$" not in joined
    # git install guard comes first
    assert "command -v git" in steps[0]


def test_workdir_default_and_sanitized():
    assert any(f"~/{DEFAULT_WORKDIR}" in s for s in provision_scope_steps(URL, "", "e", "n"))
    # a workdir with a leading slash / quotes can't escape
    steps = provision_scope_steps(URL, "/weird'", "e", "n")
    assert "'" not in "".join(s for s in steps if "git clone" in s)


def test_identity_quotes_sanitized():
    steps = provision_scope_steps(URL, "scope", "x'@p.ai", "na'me")
    email_line = next(s for s in steps if "user.email" in s)
    name_line = next(s for s in steps if "user.name" in s)
    assert email_line.count("'") == 2   # only the wrapping quotes
    assert name_line.count("'") == 2
