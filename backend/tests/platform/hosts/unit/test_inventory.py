"""Owner admission and request validation for the unexposed transport inventory."""

import base64
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.auth.dependencies import get_current_user
from src.platform.hosts import repository as host_repository
from src.platform.hosts.router import (
    get_gateway,
    get_host_service,
    require_host_devices_enabled,
    router,
)
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


class FakeGateway:
    def __init__(self):
        self.disconnected = []

    async def disconnect(self, device):
        self.disconnected.append(str(device))


def make_client(user=None):
    repository = FakeRepository()
    app = FastAPI()
    app.include_router(router)
    signed_in = user or SimpleNamespace(user_id=OWNER, role="authenticated", is_anonymous=False)
    app.dependency_overrides[get_current_user] = lambda: signed_in
    app.dependency_overrides[require_host_devices_enabled] = lambda: None
    app.dependency_overrides[get_host_service] = lambda: HostService(repository)
    gateway = FakeGateway()
    app.dependency_overrides[get_gateway] = lambda: gateway
    client = TestClient(app)
    client.gateway = gateway
    return client, repository


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
    assert client.get("/api/v1/hosts").json() == {"devices": [], "gateway_url": None}
    assert client.delete(f"/api/v1/hosts/devices/{DEVICE}").json() == {"revoked": True}
    assert repository.calls[-1] == ("host_device_revoke", {"p_owner": OWNER, "p_device": DEVICE})
    assert client.gateway.disconnected == [str(UUID(DEVICE))]


def test_repository_allows_every_host_rpc_the_module_calls():
    """Fakes bypass the allowlist; pin it to the migration and to every caller."""
    backend = Path(__file__).resolve().parents[4]
    called = set()
    for source in (backend / "src/platform/hosts").glob("*.py"):
        if source.name != "repository.py":
            # Every RPC name passed to repository.command(...) / run_blocking(command, ...).
            called |= set(re.findall(r"command[(,]\s*(?:self\.repository,\s*)?[\"'](host_[a-z_]+)[\"']",
                                     source.read_text()))
    migrations = "".join(path.read_text() for path in sorted(
        (backend.parent / "supabase/migrations").glob("*_expand_*host*.sql")))
    # host_capability_for is an internal helper, never called by the service.
    defined = set(re.findall(r"CREATE (?:OR REPLACE )?FUNCTION public\.(host_[a-z_]+)\(", migrations)) \
        - {"host_capability_for"}
    assert "host_file_event_record" in called
    assert called <= host_repository.HOST_COMMANDS
    assert defined == host_repository.HOST_COMMANDS

    class Client:
        def __init__(self):
            self.names = []

        def rpc(self, name, params):
            self.names.append(name)
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=True))

    client = Client()
    for name in sorted(defined):
        assert host_repository.HostRepository(client).command(name) is True
    assert client.names == sorted(defined)


def test_hosts_serve_only_a_persons_own_sign_in():
    """Anonymous sessions and anything that is not a person's sign-in never reach a computer."""
    for user in (SimpleNamespace(user_id=OWNER, role="authenticated", is_anonymous=True),
                 SimpleNamespace(user_id=OWNER, role="anon", is_anonymous=False),
                 SimpleNamespace(user_id=OWNER, role="service_role", is_anonymous=False)):
        client, repository = make_client(user)
        for response in (client.get("/api/v1/hosts"),
                         client.post("/api/v1/hosts/devices", json={"public_key": "A" * 44, "name": "Mac",
                                                                    "platform": "darwin"}),
                         client.post(f"/api/v1/hosts/devices/{DEVICE}/ssh-certificate",
                                     json={"public_key": "ssh-ed25519 " + "A" * 68})):
            assert (response.status_code, response.json()["detail"]) == (403, {"code": "person_required"})
        assert repository.calls == []
