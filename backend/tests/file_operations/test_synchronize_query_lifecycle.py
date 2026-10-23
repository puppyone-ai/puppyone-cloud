"""Exercise the mounted public route, not the unmounted operations function."""

import threading
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.file_operations.operation_adapter import ProductOperationAdapter
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.synchronize.dependencies import get_synchronize_engine
from src.platform.synchronize.public_router import router
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter
from src.version_engine.read.native_tree_reader import NativeTreeReader
from tests.authorization_fakes import authorization_for, install_authorization
from tests.repository_hosting.unit.test_repository_snapshot import row


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "missing", "viewer", "foreign"])
async def test_public_push_owns_one_authorization_and_consistent_read(
    native_reads, monkeypatch, case
):
    ops, manager, wire, calls, _, _, commit, *_ = native_reads
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    authorization = authorization_for("p", role="viewer" if case == "viewer" else "editor")
    authorization.authorize = Mock(wraps=authorization.authorize)
    install_authorization(app, authorization)
    engine = Mock(push_execute=AsyncMock(return_value=None))
    app.dependency_overrides[get_synchronize_engine] = lambda: engine
    app.dependency_overrides[get_product_operation_adapter] = lambda: ops
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="u", role="authenticated")

    def forbid_one_shot(*args, **kwargs):
        raise AssertionError("repeated authorization or one-shot adapter read")

    monkeypatch.setattr(ProductOperationAdapter, "for_user", forbid_one_shot)
    monkeypatch.setattr(ProductOperationAdapter, "_read", forbid_one_shot)
    original = NativeTreeReader.read_file
    loop_thread = threading.get_ident()

    def read_then_change_head(reader, project_id, path):
        assert threading.get_ident() != loop_thread
        content = original(reader, project_id, path)
        wire["refs"][1] = row(
            b"refs/heads/trunk", {"kind": "oid", "oid": "f" * len(commit)}, "commit"
        )
        return content

    monkeypatch.setattr(NativeTreeReader, "read_file", read_then_change_head)
    project = "other" if case == "foreign" else "p"
    path = "missing" if case == "missing" else "file"
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/synchronize/push/{path}?project_id={project}")

    authorization.authorize.assert_called_once()
    if case in {"viewer", "foreign"}:
        assert response.status_code in {403, 404}, response.text
        manager.open_native_read.assert_not_called()
        engine.push_execute.assert_not_awaited()
        return
    assert response.status_code == 200, response.text
    assert calls.count("begin") == calls.count("release") == 1
    if case == "missing":
        assert response.json()["code"] == 1004
        engine.push_execute.assert_not_awaited()
    else:
        engine.push_execute.assert_awaited_once_with(
            path="file", commit_id=commit, content="raw\x00bytes", node_type="file"
        )
