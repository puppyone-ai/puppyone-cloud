"""Retirement is absence, including when an old caller has valid credentials."""

import ast
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.internal.dependencies import verify_internal_secret

RETIRED = {
    "resolve-path": "POST", "list": "GET", "read": "GET", "write": "PUT",
    "create": "POST", "rm": "POST", "rename": "POST", "move": "POST",
}


@pytest.mark.asyncio
async def test_old_file_routes_are_absent_even_with_internal_credentials(monkeypatch):
    from src.main import app

    def must_not_execute():
        raise AssertionError("retired route executed a dependency")

    monkeypatch.setitem(app.dependency_overrides, verify_internal_secret, must_not_execute)
    paths = {route.path for route in app.routes}
    assert not any(path.startswith("/internal/nodes") for path in paths)
    assert "/internal/table/{table_id}" in paths
    assert "/internal/mcp-runtime/tools" in paths
    assert not any(path.startswith("/internal/nodes") for path in app.openapi()["paths"])
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        for suffix, method in RETIRED.items():
            response = await client.request(
                method, f"/internal/nodes/{suffix}?project_id=p&path=file",
                headers={"X-Internal-Secret": "previously-valid", "X-Acting-User-Id": "u"},
                json={"project_id": "p", "path": "file"},
            )
            assert response.status_code == 404, (suffix, response.text)


def test_archived_handlers_cannot_be_imported_from_runtime():
    backend = Path(__file__).resolve().parents[2]
    archive = backend / "archive/internal_nodes"
    assert (archive / "router.py.archived").is_file()
    assert not list(archive.rglob("*.py"))
    for path in (backend / "src").rglob("*.py"):
        source = path.read_text()
        assert "/internal/nodes" not in source, path
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            assert not any(
                name == "archive" or name.startswith(("archive.", "backend.archive"))
                for name in names
            ), path
