"""Locate the puppyone CLI for contract tests.

The CLI lives in ``puppyone-ai/puppyone-cli`` and is not vendored here. These
tests drive the real CLI process against this repository's routers to prove the
server keeps its side of the contract, so the binary is a test fixture rather
than product code.

Resolution order, first match wins:

  1. ``$PUPPYONE_CLI_BIN``            explicit path to ``bin/puppyone.js``
  2. ``node_modules/puppyone/...``    installed npm package
  3. ``../puppyone-cli/...``          sibling checkout

When none resolve the tests skip, so a backend-only checkout stays green. CI
must make the CLI available and set ``PUPPYONE_REQUIRE_CLI=1``, which turns that
skip into a failure. Without that variable a CI image that silently lost the CLI
would report success while testing nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

ENV_BIN = "PUPPYONE_CLI_BIN"
ENV_REQUIRE = "PUPPYONE_REQUIRE_CLI"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BIN_RELATIVE = Path("bin/puppyone.js")


def _candidates() -> list[Path]:
    explicit = os.environ.get(ENV_BIN, "").strip()
    found = [Path(explicit)] if explicit else []
    found.append(_REPO_ROOT / "node_modules/puppyone" / _BIN_RELATIVE)
    found.append(_REPO_ROOT.parent / "puppyone-cli" / _BIN_RELATIVE)
    return found


@lru_cache(maxsize=1)
def _resolve() -> tuple[Path | None, str]:
    """Return the CLI entry point, or None plus the reason it is unusable."""
    candidates = _candidates()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        # A checkout without installed dependencies fails at import time with a
        # message that looks like a product bug. Report it as a missing fixture.
        if not (candidate.parents[1] / "node_modules/commander").is_dir():
            return None, (
                f"Found {candidate} but its dependencies are not installed. "
                f"Run `npm ci` in {candidate.parents[1]}."
            )
        return candidate, ""

    searched = "\n".join(f"  - {path}" for path in candidates)
    return None, (
        "puppyone CLI not found. Install it (`npm install puppyone`), check out "
        f"puppyone-cli beside this repository, or set {ENV_BIN}. Searched:\n{searched}"
    )


def cli_entry_point() -> Path:
    """Return the CLI entry point, skipping unless the environment demands it."""
    resolved, reason = _resolve()
    if resolved is not None:
        return resolved
    if os.environ.get(ENV_REQUIRE, "").strip() == "1":
        pytest.fail(f"{ENV_REQUIRE}=1 but the CLI is unusable. {reason}")
    pytest.skip(reason)


def cli_command(*args: str) -> list[str]:
    """Build an argv for invoking the CLI through node."""
    node = shutil.which("node")
    if node is None:
        if os.environ.get(ENV_REQUIRE, "").strip() == "1":
            pytest.fail(f"{ENV_REQUIRE}=1 but node is not on PATH.")
        pytest.skip("node is not on PATH")
    return [node, str(cli_entry_point()), *args]


def cli_version() -> str:
    """Report the resolved CLI version so failures name the client under test."""
    output = subprocess.run(
        cli_command("--version"), capture_output=True, text=True, timeout=60
    )
    return (output.stdout or output.stderr).strip()
