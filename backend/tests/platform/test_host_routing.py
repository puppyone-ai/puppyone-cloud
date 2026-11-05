"""API workers reach the single device gateway; nothing else can reach its internals."""

import json
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
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
async def test_forwarding_relays_results_and_device_errors():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get(SECRET_HEADER), json.loads(request.content)))
        if json.loads(request.content)["action"] == "read":
            return httpx.Response(200, json={"revision": "a" * 64})
        return httpx.Response(409, json={"detail": {"code": "conflict"}})

    forward = route(handler)
    assert await forward.dispatch(OWNER, FOLDER, "read", {"path": "a.md"}) == {"revision": "a" * 64}
    with pytest.raises(HTTPException) as conflict:
        await forward.dispatch(OWNER, FOLDER, "write", {"path": "a.md"})
    assert (conflict.value.status_code, conflict.value.detail) == (409, {"code": "conflict"})
    assert seen[0] == ("/internal/hosts/dispatch", SECRET,
                       {"owner": OWNER, "folder_id": str(FOLDER), "action": "read", "operation": {"path": "a.md"}})


@pytest.mark.asyncio
async def test_unreachable_gateway_is_offline_but_a_lost_reply_is_outcome_unknown():
    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(HTTPException) as offline:
        await route(refused).dispatch(OWNER, FOLDER, "write", {"path": "a.md"})
    assert offline.value.detail == {"code": "device_offline"}

    def dropped(request):
        raise httpx.ReadError("reset", request=request)

    with pytest.raises(HTTPException) as unknown:
        await route(dropped).dispatch(OWNER, FOLDER, "write", {"path": "a.md"})
    assert unknown.value.detail == {"code": "device_disconnected", "outcome_unknown": True}
    with pytest.raises(HTTPException) as read:
        await route(dropped).dispatch(OWNER, FOLDER, "read", {"path": "a.md"})
    assert read.value.detail["outcome_unknown"] is False


def test_gateway_internals_require_the_shared_secret(monkeypatch):
    monkeypatch.setattr(settings, "HOST_GATEWAY_INTERNAL_SECRET", SECRET)

    class Gateway:
        def __init__(self):
            self.connections = {}

        async def dispatch(self, owner, folder, action, operation):
            return {"owner": owner, "action": action}

        async def disconnect(self, device):
            self.disconnected = device

    fake = Gateway()
    monkeypatch.setattr(gateway_service, "_gateway", fake)
    client = TestClient(gateway_service.app)
    body = {"owner": OWNER, "folder_id": str(FOLDER), "action": "read", "operation": {"path": "a.md"}}
    assert client.post("/internal/hosts/dispatch", json=body).status_code == 404
    assert client.post("/internal/hosts/dispatch", json=body,
                       headers={SECRET_HEADER: "wrong"}).status_code == 404
    allowed = client.post("/internal/hosts/dispatch", json=body, headers={SECRET_HEADER: SECRET})
    assert allowed.json() == {"owner": OWNER, "action": "read"}
    assert client.post("/internal/hosts/dispatch", json={**body, "action": "chmod"},
                       headers={SECRET_HEADER: SECRET}).status_code == 400
    assert client.get("/health").json()["role"] == "device_gateway"
    assert client.get("/openapi.json").status_code == 404
