"""Owner admission and request validation for the unexposed transport inventory."""

import base64
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.auth.dependencies import get_current_user
from src.platform.hosts.router import get_host_service, require_host_devices_enabled, router
from src.platform.hosts.service import HostService

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
FOLDER = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class FakeRepository:
    def __init__(self):
        self.calls = []

    def command(self, name, **params):
        self.calls.append((name, params))
        return {"host_device_register": DEVICE, "host_folder_register": FOLDER,
                "host_device_catalog": [], "host_device_revoke": True,
                "host_folder_remove": True}[name]


def make_client():
    repository = FakeRepository()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=OWNER)
    app.dependency_overrides[require_host_devices_enabled] = lambda: None
    app.dependency_overrides[get_host_service] = lambda: HostService(repository)
    return TestClient(app), repository


def test_inventory_disabled_by_default():
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/api/v1/hosts")
    assert response.status_code == 404


def test_device_registration_uses_current_human_and_bytea_key():
    client, repository = make_client()
    key = base64.b64encode(bytes(range(32))).decode()
    response = client.post("/api/v1/hosts/devices", json={"public_key": key,
                                                           "name": "Mac", "platform": "darwin"})
    assert response.status_code == 201
    assert response.json() == {"device_id": DEVICE}
    assert repository.calls == [("host_device_register", {"p_owner": OWNER,
        "p_key": "\\x" + bytes(range(32)).hex(), "p_name": "Mac", "p_platform": "darwin"})]


def test_bad_public_key_and_unexpected_fields_fail_before_repository():
    client, repository = make_client()
    assert client.post("/api/v1/hosts/devices", json={"public_key": "A" * 44,
        "name": "Mac", "platform": "darwin"}).status_code == 400
    assert client.post("/api/v1/hosts/devices", json={"public_key": "A" * 44,
        "name": "Mac", "platform": "darwin", "owner": DEVICE}).status_code == 422
    assert repository.calls == []


def test_folder_defaults_read_only_and_never_accepts_a_path():
    client, repository = make_client()
    response = client.post(f"/api/v1/hosts/devices/{DEVICE}/folders", json={"folder_id": FOLDER, "name": "Notes"})
    assert response.status_code == 201
    assert repository.calls == [("host_folder_register", {"p_owner": OWNER,
        "p_device": DEVICE, "p_folder": FOLDER, "p_name": "Notes", "p_capabilities": ["folder.read"]})]
    assert client.post(f"/api/v1/hosts/devices/{DEVICE}/folders",
                       json={"folder_id": FOLDER, "name": "Notes", "path": "/Users/private"}).status_code == 422
    assert len(repository.calls) == 1


def test_catalog_and_revoke_use_authenticated_owner():
    client, repository = make_client()
    assert client.get("/api/v1/hosts").json() == {"devices": []}
    assert client.delete(f"/api/v1/hosts/devices/{DEVICE}").json() == {"revoked": True}
    assert repository.calls[-1] == ("host_device_revoke", {"p_owner": OWNER, "p_device": DEVICE})
    assert UUID(DEVICE)
