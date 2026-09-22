from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.context_publish.dependencies import get_context_publish_service
from src.context_publish.models import ContextPublish
from src.context_publish.repository import ContextPublishRepositoryBase
from src.context_publish.router import public_router, router as publish_router
from src.context_publish.service import ContextPublishService
from src.exception_handler import (
    app_exception_handler,
    generic_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from src.exceptions import AppException, PermissionException
from src.context_publish.supabase_schemas import (
    ContextPublishCreate as SbContextPublishCreate,
    ContextPublishUpdate as SbContextPublishUpdate,
)


class _InMemoryPublishRepo(ContextPublishRepositoryBase):
    def __init__(self):
        self._id = 0
        self._by_id: dict[int, ContextPublish] = {}
        self._by_key: dict[str, int] = {}

    def create(self, data: SbContextPublishCreate) -> ContextPublish:
        self._id += 1
        now = datetime.now(UTC)
        p = ContextPublish(
            id=self._id,
            created_at=now,
            updated_at=now,
            created_by=data.created_by,
            table_id=str(data.table_id or ""),
            json_path=str(data.json_path or ""),
            publish_key=str(data.publish_key or ""),
            status=bool(data.status),
            expires_at=data.expires_at or now,
        )
        # 模拟唯一约束
        if p.publish_key in self._by_key:
            raise ValueError("publish_key conflict")
        self._by_id[p.id] = p
        self._by_key[p.publish_key] = p.id
        return p

    def get_by_id(self, publish_id: int) -> Optional[ContextPublish]:
        return self._by_id.get(publish_id)

    def get_by_key(self, publish_key: str) -> Optional[ContextPublish]:
        pid = self._by_key.get(publish_key)
        if not pid:
            return None
        return self._by_id.get(pid)

    def list_by_created_by(self, created_by: str, *, skip: int = 0, limit: int = 100):
        items = [p for p in self._by_id.values() if (p.created_by or "") == created_by]
        items.sort(key=lambda x: x.created_at, reverse=True)
        return items[skip : skip + limit]

    def update(self, publish_id: int, data: SbContextPublishUpdate) -> Optional[ContextPublish]:
        existing = self._by_id.get(publish_id)
        if not existing:
            return None
        now = datetime.now(UTC)
        updated = existing.model_copy(
            update={
                "updated_at": now,
                "status": existing.status if data.status is None else bool(data.status),
                "expires_at": existing.expires_at if data.expires_at is None else data.expires_at,
            }
        )
        self._by_id[publish_id] = updated
        return updated

    def delete(self, publish_id: int) -> bool:
        existing = self._by_id.pop(publish_id, None)
        if not existing:
            return False
        self._by_key.pop(existing.publish_key, None)
        return True


class _FakeTableService:
    def __init__(self):
        self.get_by_id = Mock()
        self.get_context_data = Mock()


class _FakeAuthorization:
    def __init__(self):
        self.authorize = Mock(return_value=object())


@pytest.fixture
def current_user():
    return CurrentUser(
        user_id="u_test",
        email="test@example.com",
        role="authenticated",
        is_anonymous=False,
        app_metadata={},
        user_metadata={},
    )


@pytest.fixture
def app(current_user):
    test_app = FastAPI()
    test_app.add_exception_handler(AppException, app_exception_handler)  # type: ignore
    test_app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore
    test_app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore
    test_app.add_exception_handler(Exception, generic_exception_handler)  # type: ignore

    test_app.include_router(publish_router, prefix="/api/v1")
    test_app.include_router(public_router)

    repo = _InMemoryPublishRepo()
    table_service = _FakeTableService()
    table_service.get_by_id.return_value = SimpleNamespace(project_id="project-1")
    table_service.get_context_data.return_value = {"hello": "world"}
    authorization = _FakeAuthorization()
    svc = ContextPublishService(
        repo=repo,
        table_service=table_service,  # type: ignore[arg-type]
        authorization=authorization,  # type: ignore[arg-type]
    )

    test_app.dependency_overrides[get_context_publish_service] = lambda: svc
    test_app.dependency_overrides[get_current_user] = lambda: current_user

    return test_app


@pytest.fixture
def client(app: FastAPI):
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_create_publish_and_public_get_success(client: TestClient):
    resp = client.post("/api/v1/publishes/", json={"table_id": "123", "json_path": "/users"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == 0
    url = body["data"]["url"]
    assert "/p/" in url
    publish_key = body["data"]["publish_key"]

    # 第一次公开读取：成功（并命中缓存）
    resp2 = client.get(f"/p/{publish_key}")
    assert resp2.status_code == 200
    assert resp2.headers["content-type"].startswith("application/json")
    assert resp2.json() == {"hello": "world"}

    # 第二次公开读取：仍成功（大概率命中缓存，但不强依赖）
    resp3 = client.get(f"/p/{publish_key}")
    assert resp3.status_code == 200


def test_publish_management_rechecks_current_project_grant(app: FastAPI, client: TestClient):
    svc = app.dependency_overrides[get_context_publish_service]()
    created = client.post(
        "/api/v1/publishes/",
        json={"table_id": "123", "json_path": "/users"},
    )
    assert created.status_code == 201
    publish_id = created.json()["data"]["id"]

    svc.authorization.authorize.side_effect = PermissionException("revoked")

    denied_update = client.patch(
        f"/api/v1/publishes/{publish_id}", json={"status": False}
    )
    hidden_list = client.get("/api/v1/publishes/")

    assert denied_update.status_code == 403
    assert hidden_list.status_code == 200
    assert hidden_list.json()["data"] == []


def test_revoke_then_public_get_404_even_if_cached(client: TestClient):
    resp = client.post("/api/v1/publishes/", json={"table_id": "123", "json_path": ""})
    assert resp.status_code == 201
    publish_id = resp.json()["data"]["id"]
    publish_key = resp.json()["data"]["publish_key"]

    # 先读取一次以填充缓存
    ok = client.get(f"/p/{publish_key}")
    assert ok.status_code == 200

    # revoke（禁用）
    resp2 = client.patch(f"/api/v1/publishes/{publish_id}", json={"status": False})
    assert resp2.status_code == 200

    # 再读：必须 404（缓存已失效且语义生效）
    resp3 = client.get(f"/p/{publish_key}")
    assert resp3.status_code == 404
