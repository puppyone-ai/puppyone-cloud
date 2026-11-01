"""Backend dependencies point one way. Run `uv run lint-imports` for the full report."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BUSINESS_CONTRACT = "Business modules point downward"

# Lower this when an entry leaves ignore_imports. It only ever goes down.
KNOWN_BUSINESS_VIOLATIONS = 42


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


def test_only_the_business_contract_has_known_violations():
    exempt = [contract["name"] for contract in _contracts() if "ignore_imports" in contract]
    assert exempt == [BUSINESS_CONTRACT]


def test_known_business_violations_only_shrink():
    contract = next(item for item in _contracts() if item["name"] == BUSINESS_CONTRACT)
    assert len(contract["ignore_imports"]) == KNOWN_BUSINESS_VIOLATIONS


def test_every_top_level_module_has_a_layer():
    layers = _contracts()[0]["layers"]
    layered = {name.strip() for layer in layers for name in layer.split(":")}
    source = BACKEND_ROOT / "src"
    modules = {path.stem for path in source.glob("*.py") if path.stem != "__init__"}
    packages = {path.parent.name for path in source.glob("*/__init__.py")}
    assert {f"src.{name}" for name in modules | packages} <= layered
