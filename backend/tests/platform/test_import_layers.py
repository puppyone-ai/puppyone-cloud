"""Backend dependencies point one way. Run `uv run lint-imports` for the full report."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Lower this when an entry leaves ignore_imports. It only ever goes down.
KNOWN_VIOLATIONS = 50


def _ignored_imports() -> list[str]:
    config = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["importlinter"]["contracts"][0]["ignore_imports"]


def test_dependencies_only_point_downward():
    result = subprocess.run(
        [str(Path(sys.executable).with_name("lint-imports")), "--no-cache"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_known_violations_only_shrink():
    assert len(_ignored_imports()) == KNOWN_VIOLATIONS
