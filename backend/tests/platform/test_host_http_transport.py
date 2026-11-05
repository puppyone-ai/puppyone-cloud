"""Single-process ASGI transport test; not a deployed two-device acceptance test."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.hosts.gateway import GATEWAY_INSTANCE, HostGateway
import src.platform.hosts.router as hosts_router
from src.platform.hosts.router import get_gateway, require_host_devices_enabled, router

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
FOLDER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
EPOCH = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


class Repository:
    revoked = False

    def command(self, name, **params):
        if name == "host_folder_admit":
            if self.revoked or params["p_actor"] != OWNER or params["p_folder"] != str(FOLDER):
                raise ValueError("denied")
            return {"device_id": str(DEVICE), "epoch": str(EPOCH),
                    "gateway_instance": GATEWAY_INSTANCE}
        if name == "host_connection_close":
            return True
        if name == "host_connection_renew":
            return not self.revoked
        raise AssertionError(name)


class Proof:
    def accept(self, owner, device, challenge, epoch, nonce, signature, gateway):
        assert (owner, device, epoch, gateway) == (OWNER, DEVICE, EPOCH, GATEWAY_INSTANCE)


def test_websocket_to_http_file_route_and_owner_revocation(monkeypatch):
    monkeypatch.setattr(settings, "HOST_DEVICES_ENABLED", True)
    repository = Repository()
    gateway = HostGateway(repository, Proof())
    monkeypatch.setattr(hosts_router, "_gateway", gateway)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_host_devices_enabled] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=OWNER, role="authenticated")
    app.dependency_overrides[get_gateway] = lambda: gateway
    with TestClient(app) as client, client.websocket_connect(f"/api/v1/hosts/devices/{DEVICE}/connect") as socket:
        socket.send_json({"owner": OWNER, "challenge_id": str(FOLDER), "epoch": str(EPOCH),
                          "nonce": "proof", "signature": "signature"})
        assert socket.receive_json() == {"type": "ready", "epoch": str(EPOCH)}
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(client.get, f"/api/v1/hosts/folders/{FOLDER}/file?path=note.md")
            frame = socket.receive_json()
            assert frame["type"] == "file"
            assert (frame["folder_id"], frame["action"], frame["path"]) == (str(FOLDER), "read", "note.md")
            socket.send_json({"type": "reply", "id": frame["id"],
                              "result": {"content_base64": "bm90ZQ==", "revision": "a" * 64}})
            response = request.result(timeout=5)
            assert response.status_code == 200
            assert response.json()["content_base64"] == "bm90ZQ=="
            assert response.headers["cache-control"] == "no-store"
        repository.revoked = True
        denied = client.get(f"/api/v1/hosts/folders/{FOLDER}/file?path=note.md")
        assert denied.status_code == 403
