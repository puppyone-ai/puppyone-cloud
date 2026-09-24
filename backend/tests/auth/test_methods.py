import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.auth import methods


@pytest.mark.parametrize("enabled", [["email"], ["email", "google", "github"]])
def test_only_configured_methods_are_published_and_cached(monkeypatch, enabled):
    calls = []
    real_client = httpx.AsyncClient

    def provider(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "external": {name: name in enabled for name in ["email", "google", "github"]},
                "provider_secret": "never-return",
            },
        )

    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-key")
    monkeypatch.setattr(methods, "_cached", None)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(provider), **kwargs),
    )
    app = FastAPI()
    app.include_router(methods.router)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get("/methods")
            assert response.status_code == 200
            assert response.json()["data"] == {"providers": enabled}
            assert "no-store" in response.headers["cache-control"]
            assert "never-return" not in response.text
    assert len(calls) == 1


def test_provider_outage_does_not_pretend_oauth_enabled(monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setenv("SUPABASE_URL", "https://auth.example.test")
    monkeypatch.setattr(methods, "_cached", None)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500, text="secret")),
            **kwargs,
        ),
    )
    app = FastAPI()
    app.include_router(methods.router)
    with TestClient(app) as client:
        response = client.get("/methods")
        assert response.status_code == 503
        assert "secret" not in response.text
