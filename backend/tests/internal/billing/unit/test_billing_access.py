"""Owner authority plus legacy space-kind metadata remain compatible for billing.
New seat plans accept either historical kind.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.internal.dependencies import verify_internal_secret
from src.internal.router import router


def _client(*, role: str | None, personal_owner_id: str | None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_internal_secret] = lambda: None
    repository = SimpleNamespace(
        get_member=lambda org_id, user_id: SimpleNamespace(role=role) if role else None,
        get_by_id=lambda org_id: SimpleNamespace(personal_owner_id=personal_owner_id),
    )
    patcher = patch("src.internal.router.OrganizationRepository", return_value=repository)
    patcher.start()
    client = TestClient(app)
    client.__dict__["_patcher"] = patcher
    return client


def _access(client: TestClient):
    try:
        return client.get("/internal/billing/organizations/org-1/access", params={"user_id": "u1"})
    finally:
        client.__dict__["_patcher"].stop()


def test_a_personal_space_owner_is_told_the_space_is_personal() -> None:
    response = _access(_client(role="owner", personal_owner_id="u1"))
    assert response.status_code == 200
    assert response.json()["space_kind"] == "personal"


def test_a_team_owner_is_told_the_space_is_a_team() -> None:
    response = _access(_client(role="owner", personal_owner_id=None))
    assert response.status_code == 200
    assert response.json()["space_kind"] == "team"


def test_only_the_owner_may_change_billing() -> None:
    assert _access(_client(role="member", personal_owner_id=None)).status_code == 403
    assert _access(_client(role=None, personal_owner_id=None)).status_code == 403
