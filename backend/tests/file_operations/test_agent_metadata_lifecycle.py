from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.file_operations.operation_adapter import ProductOperationAdapter
from src.platform.access.adapters.agent.config.dependencies import get_agent_config_service
from src.platform.access.adapters.agent.config.models import Agent, AgentBash
from src.platform.access.adapters.agent.config.router import router
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter
from src.version_engine.read.native_tree_reader import NativeTreeReader
from tests.authorization_fakes import authorization_for, install_authorization
from tests.repository_hosting.unit.test_repository_snapshot import row


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["metadata", "denied", "unavailable", "empty"])
async def test_agent_list_reuses_entry_grant_and_one_snapshot(native_reads, monkeypatch, case):
    ops, manager, wire, calls, _, _, commit, *_ = native_reads
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    auth = authorization_for("p", role="viewer")
    auth.authorize = Mock(wraps=auth.authorize)
    install_authorization(app, auth)
    now = datetime.now(UTC)
    agents = [] if case == "empty" else [Agent(
        id=f"a-{i}", project_id="p", name=f"Agent {i}", created_at=now, updated_at=now,
        bash_accesses=[AgentBash(
            id=f"b-{i}-{path}", agent_id=f"a-{i}", path=path, created_at=now,
        ) for path in ("file", "dir", "missing")],
    ) for i in range(35)]
    service = Mock()
    service.list_agents.return_value = agents
    app.dependency_overrides[get_agent_config_service] = lambda: service
    app.dependency_overrides[get_product_operation_adapter] = lambda: ops
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="u", role="authenticated")
    monkeypatch.setattr(ProductOperationAdapter, "for_user", Mock(side_effect=AssertionError("duplicate authorization")))
    monkeypatch.setattr(ProductOperationAdapter, "_read", Mock(side_effect=AssertionError("one-shot read")))
    original = NativeTreeReader.stat
    observed = []

    def stat(reader, project, path, **kwargs):
        observed.append(path)
        if case == "unavailable":
            raise RuntimeError("storage unavailable")
        result = original(reader, project, path, **kwargs)
        wire["refs"][1] = row(b"refs/heads/trunk", {"kind": "oid", "oid": "f" * len(commit)}, "commit")
        return result

    monkeypatch.setattr(NativeTreeReader, "stat", stat)
    target = "foreign" if case == "denied" else "p"
    async with AsyncClient(
        transport=ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/agent-config/?project_id={target}")
    auth.authorize.assert_called_once()
    if case == "denied":
        assert response.status_code == 404
        service.list_agents.assert_not_called()
        manager.open_native_read.assert_not_called()
    elif case == "empty":
        assert response.status_code == 200 and response.json()["data"] == []
        manager.open_native_read.assert_not_called()
    else:
        service.list_agents.assert_called_once_with("p", viewer_user_id="u")
        assert calls.count("begin") == calls.count("release") == 1
        if case == "unavailable":
            assert response.status_code == 500
        else:
            assert response.status_code == 200, response.text
            assert sorted(observed) == ["dir", "file", "missing"]
            bindings = response.json()["data"][0]["bash_accesses"]
            assert [(b["node_name"], b["node_type"]) for b in bindings] == [
                ("file", "file"), ("dir", "folder"), (None, None),
            ]
