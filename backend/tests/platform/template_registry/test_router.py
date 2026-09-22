from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.project.models import Project
from src.platform.template_registry.config import TemplateRegistrySettings
from src.platform.template_registry.dependencies import (
    get_template_instantiation_service,
    get_template_registry_service,
)
from src.platform.template_registry.instantiation import TemplateInstantiationResult
from src.platform.template_registry.providers.builtin import (
    BuiltinTemplateRegistryProvider,
)
from src.platform.template_registry.router import router
from src.platform.template_registry.service import TemplateRegistryService
from tests.authorization_fakes import authorization_for


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


def test_catalog_and_detail_routes_use_provider_neutral_contract() -> None:
    settings = TemplateRegistrySettings(_env_file=None)
    registry = TemplateRegistryService(
        provider=BuiltinTemplateRegistryProvider(settings), settings=settings
    )
    app = _app()
    app.dependency_overrides[get_template_registry_service] = lambda: registry

    with TestClient(app) as client:
        status = client.get("/api/v1/templates/status")
        catalog = client.get("/api/v1/templates")
        detail = client.get("/api/v1/templates/get-started")

    assert status.status_code == 200
    assert status.json()["data"]["source"] == "builtin"
    assert catalog.status_code == 200
    assert catalog.json()["data"]["templates"][0]["current_release"]["id"]
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == "get-started"


def test_instantiate_route_returns_authorized_project(monkeypatch) -> None:
    project = Project(
        id="project-1",
        name="Hello copy",
        description="Copied",
        org_id="org-1",
        created_by="user-1",
        created_at=datetime.now(UTC),
    )

    class Instantiation:
        async def instantiate(self, **kwargs):
            assert kwargs["template_id"] == "hello"
            assert kwargs["release_id"] == "1.0.0"
            assert kwargs["org_id"] == "org-1"
            assert kwargs["operation_key"] == "123e4567-e89b-42d3-a456-426614174000"
            return TemplateInstantiationResult(
                template_id="hello", release_id="1.0.0", project=project
            )

    app = _app()
    app.dependency_overrides[get_template_instantiation_service] = Instantiation
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user-1", role="authenticated"
    )
    app.dependency_overrides[get_authorization_service] = lambda: authorization_for("project-1")
    monkeypatch.setattr(
        "src.platform.template_registry.router.resolve_org_id",
        lambda requested, _user_id: requested or "org-1",
    )

    response = TestClient(app).post(
        "/api/v1/templates/hello/instantiate",
        json={"org_id": "org-1", "release_id": "1.0.0"},
        headers={"Idempotency-Key": "123e4567-e89b-42d3-a456-426614174000"},
    )

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["release_id"] == "1.0.0"
    assert body["project"]["id"] == "project-1"
    assert body["project"]["effective_role"] == "admin"
    assert response.headers["Idempotency-Replayed"] == "false"


def test_instantiate_route_requires_explicit_organization() -> None:
    app = _app()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="user-1", role="authenticated"
    )

    response = TestClient(app).post(
        "/api/v1/templates/hello/instantiate",
        json={"release_id": "1.0.0"},
        headers={"Idempotency-Key": "123e4567-e89b-42d3-a456-426614174000"},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "org_id"
