"""POST /auth/initialize: the whole first sign-in, through its real HTTP route."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.onboarding.dependencies import get_onboarding_service
from src.platform.onboarding.router import router as onboarding_router
from src.platform.onboarding.service import OnboardingService

USER = CurrentUser(
    user_id="user-1",
    email="new@example.com",
    role="authenticated",
    user_metadata={"full_name": "New User"},
)


class Steps:
    """Records the order first sign-in runs its steps in."""

    def __init__(self, demo_project_id="demo-project"):
        self.order = []
        self.ensure_args = None
        self.demo_project_id = demo_project_id

    def ensure_initialized(self, **kwargs):
        self.order.append("account")
        self.ensure_args = kwargs
        return {"org_id": "org-1", "is_new_org": True}

    async def provision(self, **kwargs):
        self.order.append(("entitlements", kwargs))

    async def maybe_seed_demo_project(self, **kwargs):
        self.order.append(("demo", kwargs))
        return self.demo_project_id


@pytest.fixture
def first_sign_in(monkeypatch):
    def build(entitlements_mode="db", demo_project_id="demo-project"):
        steps = Steps(demo_project_id)
        monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", entitlements_mode)
        monkeypatch.setattr(
            "src.platform.billing.provisioning.get_entitlement_provisioning_service",
            lambda: SimpleNamespace(ensure=steps.provision),
        )
        service = OnboardingService(
            initialization=steps,
            profile_repo=MagicMock(),
            project_service=MagicMock(),
            project_control_plane=MagicMock(),
        )
        service.maybe_seed_demo_project = steps.maybe_seed_demo_project
        app = FastAPI()
        app.include_router(onboarding_router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: USER
        app.dependency_overrides[get_onboarding_service] = lambda: service
        return TestClient(app), steps

    return build


def test_a_new_user_gets_an_account_then_entitlements_then_a_demo_project(first_sign_in):
    client, steps = first_sign_in()

    response = client.post("/api/v1/auth/initialize")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "org_id": "org-1",
        "is_new_org": True,
        "demo_project_id": "demo-project",
    }
    # The demo Project is admitted against the entitlements provisioned before it.
    assert steps.order == [
        "account",
        ("entitlements", {"org_id": "org-1", "actor_user_id": "user-1"}),
        ("demo", {"user_id": "user-1", "org_id": "org-1"}),
    ]


def test_the_account_is_created_from_the_signed_in_identity(first_sign_in):
    client, steps = first_sign_in()

    client.post("/api/v1/auth/initialize")

    assert steps.ensure_args == {
        "user_id": "user-1",
        "email": "new@example.com",
        "display_name": "New User",
    }


def test_entitlements_are_provisioned_only_where_the_database_owns_them(first_sign_in):
    client, steps = first_sign_in(entitlements_mode="config")

    response = client.post("/api/v1/auth/initialize")

    assert response.status_code == 200, response.text
    assert [step if isinstance(step, str) else step[0] for step in steps.order] == [
        "account",
        "demo",
    ]


def test_a_returning_user_lands_without_a_demo_project(first_sign_in):
    client, _steps = first_sign_in(demo_project_id=None)

    response = client.post("/api/v1/auth/initialize")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["demo_project_id"] is None


def test_a_failed_entitlement_step_does_not_seed_a_demo_project(monkeypatch, first_sign_in):
    client, steps = first_sign_in()
    monkeypatch.setattr(
        "src.platform.billing.provisioning.get_entitlement_provisioning_service",
        lambda: SimpleNamespace(ensure=AsyncMock(side_effect=ConnectionError("unavailable"))),
    )

    with pytest.raises(ConnectionError):
        client.post("/api/v1/auth/initialize")

    assert steps.order == ["account"]


def test_signing_in_is_required(monkeypatch):
    monkeypatch.setattr(settings, "SKIP_AUTH", False)
    app = FastAPI()
    app.include_router(onboarding_router, prefix="/api/v1")
    app.dependency_overrides[get_onboarding_service] = lambda: MagicMock()

    with TestClient(app) as client:
        response = client.post("/api/v1/auth/initialize")

    assert response.status_code == 401
