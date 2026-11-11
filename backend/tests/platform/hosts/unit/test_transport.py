"""Single-process ASGI transport test; not a deployed two-device acceptance test."""

import base64
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.platform.hosts.router as hosts_router
from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.hosts.gateway import GATEWAY_INSTANCE, HostGateway
from src.platform.hosts.router import get_gateway, require_host_devices_enabled, router

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
FOLDER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
EPOCH = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
BOOT = "99999999-9999-4999-8999-999999999999"


class Repository:
    revoked = False

    def command(self, name, **params):
        if name == "host_file_event_record":
            assert params["p_actor"] == OWNER
            return True
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
    def accept(self, owner, device, challenge, epoch, nonce, signature, gateway, **hello):
        assert hello == {"protocol": 1, "boot_id": UUID(BOOT), "app_version": "1.4.0"}
        assert (owner, device, epoch, gateway) == (OWNER, DEVICE, EPOCH, GATEWAY_INSTANCE)


def test_device_socket_carries_tunnel_bytes_and_owner_revocation_ends_access(monkeypatch):
    monkeypatch.setattr(settings, "HOST_DEVICES_ENABLED", True)
    repository = Repository()
    gateway = HostGateway(repository, Proof())
    monkeypatch.setattr(hosts_router, "_gateway", gateway)

    class Auth:
        def get_current_user(self, token):
            return CurrentUser(user_id=OWNER, role="authenticated") if token == "owner-token" else None

    import src.platform.auth.dependencies as auth_dependencies
    monkeypatch.setattr(auth_dependencies, "get_auth_service", lambda: Auth())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_host_devices_enabled] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=OWNER, role="authenticated")
    app.dependency_overrides[get_gateway] = lambda: gateway
    tunnel_path = f"/api/v1/hosts/folders/{FOLDER}/tunnel"
    with TestClient(app) as client, client.websocket_connect(f"/api/v1/hosts/devices/{DEVICE}/connect") as socket:
        socket.send_json({"owner": OWNER, "challenge_id": str(FOLDER), "epoch": str(EPOCH),
                          "nonce": "proof", "signature": "signature",
                          "protocol": 1, "boot_id": BOOT, "app_version": "1.4.0"})
        assert socket.receive_json() == {"type": "ready", "epoch": str(EPOCH)}
        # No plaintext file route exists for a computer's folder.
        assert client.get(f"/api/v1/hosts/folders/{FOLDER}/file?path=note.md").status_code == 404
        with ThreadPoolExecutor(max_workers=1) as pool:
            def device_side():
                frame = socket.receive_json()
                assert frame == {"type": "tunnel_open", "stream": frame["stream"], "folder_id": str(FOLDER)}
                socket.send_json({"type": "tunnel_opened", "stream": frame["stream"]})
                data = socket.receive_json()
                assert data["type"] == "tunnel_data" and data["stream"] == frame["stream"]
                socket.send_json({"type": "tunnel_data", "stream": frame["stream"], "data": "U1NILTIuMA=="})
                return base64.b64decode(data["data"])
            served = pool.submit(device_side)
            with client.websocket_connect(tunnel_path, subprotocols=["version.bearer.owner-token"]) as tunnel:
                assert tunnel.receive_json() == {"type": "ready"}
                tunnel.send_bytes(b"ciphertext")
                assert tunnel.receive_bytes() == b"SSH-2.0"
                assert served.result(timeout=5) == b"ciphertext"
        repository.revoked = True
        with client.websocket_connect(tunnel_path, subprotocols=["version.bearer.owner-token"]) as denied:
            assert denied.receive_json() == {"type": "error", "code": "folder_unavailable"}
