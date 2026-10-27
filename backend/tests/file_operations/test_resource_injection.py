"""The actual FastAPI dependency graph shares resources, not request identity."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from src.content.table.dependencies import get_table_repository, get_table_service
from src.infra.supabase.dependencies import get_supabase_client, get_supabase_repository
from src.platform.authorization.dependencies import get_authorization_service
from src.tool.dependencies import get_tool_service
from src.version_engine.bootstrap.container import VersionEngineContainer


@pytest.mark.asyncio
async def test_request_services_share_engine_and_keep_users_isolated():
    app = FastAPI()
    manager = object()
    app.state.version_engine = VersionEngineContainer(manager, object())
    for dependency in (get_supabase_client, get_supabase_repository, get_table_repository):
        app.dependency_overrides[dependency] = lambda: SimpleNamespace()
    app.dependency_overrides[get_authorization_service] = lambda: SimpleNamespace()
    observed = []

    @app.get("/probe")
    def probe(
        tool=Depends(get_tool_service),
        table=Depends(get_table_service),
    ):
        observed.append((tool, table))
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        for _ in range(2):
            assert (await client.get("/probe")).status_code == 200
    for tool, table in observed:
        assert tool._ops._repos is table._repos is manager
    assert observed[0][0].authorization is not observed[1][0].authorization
    observed[0][1].bind_user("first-user")
    assert observed[1][1]._user_id is None


def test_request_factories_do_not_bootstrap_another_engine():
    src = Path(__file__).resolve().parents[2] / "src"
    paths = [
        "tool/dependencies.py",
        "content/table/dependencies.py",
        "platform/repository_target/scope_router.py",
        "platform/synchronize/router.py",
    ]
    for name in paths:
        for node in ast.walk(ast.parse((src / name).read_text())):
            if isinstance(node, ast.Name):
                assert node.id not in {
                    "build_worker_version_engine_container",
                    "build_version_engine_container",
                }, name
    for path in (src / "infra/execution").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("src.platform", "src.version_engine", "src.file_operations")
                )
