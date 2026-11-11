"""Release acceptance prepares its account the way a new user does."""

import httpx
import pytest

from src.infra.data_migrations.release import acceptance


def test_claims_the_trial_before_it_needs_agent_credit(monkeypatch):
    seen = []

    def respond(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "token"})
        if request.url.path.endswith("/ai/trial"):
            return httpx.Response(200, json={"balance_micro_usd": 1})
        return httpx.Response(500)

    client = httpx.Client
    monkeypatch.setattr(
        acceptance.httpx,
        "Client",
        lambda **options: client(transport=httpx.MockTransport(respond), **options),
    )

    with pytest.raises(RuntimeError):
        acceptance.accept(
            {
                "api_url": "https://cloud.example",
                "email": "release@example.com",
                "password": "secret",
                "org_id": "org",
            }
        )

    assert seen == [
        ("GET", "/health"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/ai/trial"),
        ("POST", "/api/v1/projects/"),
    ]
