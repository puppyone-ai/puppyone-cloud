from __future__ import annotations

import tomllib
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[3]


def test_installed_database_cli_includes_runtime_packages() -> None:
    pyproject = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text())

    package_find = pyproject["tool"]["setuptools"]["packages"]["find"]
    assert package_find["where"] == ["."]
    assert package_find["namespaces"] is False
    assert "src*" in package_find["include"]
    assert "mcp_service*" in package_find["include"]
