"""Backend dependencies point one way. Run `uv run lint-imports` for the full report."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _contracts() -> list[dict]:
    config = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["importlinter"]["contracts"]


def test_dependencies_only_point_downward():
    result = subprocess.run(
        [str(Path(sys.executable).with_name("lint-imports")), "--no-cache"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_import_is_exempt():
    assert not any("ignore_imports" in contract for contract in _contracts())


def test_every_top_level_module_has_a_layer():
    layers = _contracts()[0]["layers"]
    layered = {name.strip() for layer in layers for name in layer.split(":")}
    source = BACKEND_ROOT / "src"
    modules = {path.stem for path in source.glob("*.py") if path.stem != "__init__"}
    packages = {path.parent.name for path in source.glob("*/__init__.py")}
    assert {f"src.{name}" for name in modules | packages} <= layered
