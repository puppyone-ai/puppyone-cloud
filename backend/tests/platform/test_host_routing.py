"""API workers reach the single device gateway; nothing else can reach its internals."""

from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

import src.platform.hosts.gateway_service as gateway_service
from src.config import Settings, settings
from src.platform.hosts.routing import SECRET_HEADER, ForwardingRoute, gateway_secret_matches

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
FOLDER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
SECRET = "s" * 40


def hosted(**values):
    return Settings.model_construct(APP_ENV="production", HOST_DEVICES_ENABLED=True,
                                    JWT_SECRET="j" * 40, INTERNAL_API_SECRET="i" * 40, **values)


def test_hosted_device_hosts_require_a_dedicated_gateway():
    assert Settings.model_construct(APP_ENV="production", HOST_DEVICES_ENABLED=False) \
        .enforce_device_gateway_safety()
    with pytest.raises(ValueError, match="HOST_GATEWAY_INTERNAL_SECRET"):
        hosted(SERVICE_ROLE="api").enforce_device_gateway_safety()
    with pytest.raises(ValueError, match="HOST_GATEWAY_INTERNAL_SECRET"):
        hosted(SERVICE_ROLE="api", HOST_GATEWAY_INTERNAL_SECRET="j" * 40).enforce_device_gateway_safety()
    with pytest.raises(ValueError, match="HOST_GATEWAY_INTERNAL_URL"):
        hosted(SERVICE_ROLE="api", HOST_GATEWAY_INTERNAL_SECRET=SECRET).enforce_device_gateway_safety()
    with pytest.raises(ValueError, match="https"):
        hosted(SERVICE_ROLE="api", HOST_GATEWAY_INTERNAL_SECRET=SECRET,
               HOST_GATEWAY_INTERNAL_URL="http://gateway.internal:8080",
               HOST_GATEWAY_PUBLIC_URL="http://devices.example").enforce_device_gateway_safety()
    # Every worker signs SSH user certificates with the one authority hosts trust.
    with pytest.raises(ValueError, match="HOST_SSH_CA_PRIVATE_KEY"):
        hosted(SERVICE_ROLE="device_gateway", HOST_GATEWAY_INTERNAL_SECRET=SECRET,
               HOST_GATEWAY_PUBLIC_URL="https://devices.puppyone.ai").enforce_device_gateway_safety()
    import asyncssh

    authority = asyncssh.generate_private_key("ssh-ed25519").export_private_key("openssh").decode()
    assert hosted(SERVICE_ROLE="device_gateway", HOST_GATEWAY_INTERNAL_SECRET=SECRET,
                  HOST_GATEWAY_PUBLIC_URL="https://devices.puppyone.ai",
                  HOST_SSH_CA_PRIVATE_KEY=authority).enforce_device_gateway_safety()


def test_secret_comparison_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "HOST_GATEWAY_INTERNAL_SECRET", "")
    assert not gateway_secret_matches("")
    monkeypatch.setattr(settings, "HOST_GATEWAY_INTERNAL_SECRET", SECRET)
    assert gateway_secret_matches(SECRET)
    assert not gateway_secret_matches(None)
    assert not gateway_secret_matches(SECRET + "x")


def route(handler):
    return ForwardingRoute("http://gateway.internal:8080/", SECRET,
                           client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_revocation_reaches_the_gateway_with_the_shared_secret(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get(SECRET_HEADER)))
        return httpx.Response(200, json={"disconnected": True})

    published = []
    monkeypatch.setattr("src.platform.hosts.routing.publish_revocation", lambda device: published.append(device) or _done())
    await route(handler).revoked(UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))
    assert seen == [("POST", "/internal/hosts/devices/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/disconnect", SECRET)]
    assert published


async def _done():
    return None


def test_gateway_internals_require_the_shared_secret(monkeypatch):
    monkeypatch.setattr(settings, "HOST_GATEWAY_INTERNAL_SECRET", SECRET)

    class Gateway:
        def __init__(self):
            self.connections = {}

        async def disconnect(self, device):
            self.disconnected = device

    fake = Gateway()
    monkeypatch.setattr(gateway_service, "_gateway", fake)
    client = TestClient(gateway_service.app)
    path = "/internal/hosts/devices/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/disconnect"
    assert client.post(path).status_code == 404
    assert client.post(path, headers={SECRET_HEADER: "wrong"}).status_code == 404
    assert client.post(path, headers={SECRET_HEADER: SECRET}).json() == {"disconnected": True}
    # The relay's internal routes are gone.
    assert client.post("/internal/hosts/dispatch", json={}, headers={SECRET_HEADER: SECRET}).status_code == 404
    assert client.get("/internal/hosts/events", headers={SECRET_HEADER: SECRET}).status_code == 404
    assert client.get("/health").json()["role"] == "device_gateway"
    assert client.get("/openapi.json").status_code == 404
