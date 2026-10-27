"""The CLI must not come back into this repository.

A vendored copy under cli/ drifted from puppyone-ai/puppyone-cli for about three
months, because nothing failed when the two disagreed. The CLI is a client; this
repository only drives it as a contract-test fixture and resolves it from the
published package or a sibling checkout.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_DIR = REPO_ROOT / "cli"
ALLOWED = {"README.md"}


def test_cli_sources_are_not_vendored():
    if not CLI_DIR.exists():
        return
    tracked = {
        path.relative_to(CLI_DIR).as_posix()
        for path in CLI_DIR.rglob("*")
        if path.is_file() and "node_modules" not in path.parts
    }
    unexpected = sorted(tracked - ALLOWED)
    assert not unexpected, (
        "CLI sources reappeared under cli/. The CLI lives in "
        "puppyone-ai/puppyone-cli and is resolved through "
        "backend/tests/support/cli_binary.py. Unexpected files:\n"
        + "\n".join(f"  - {name}" for name in unexpected)
    )
