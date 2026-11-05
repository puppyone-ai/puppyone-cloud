"""Devices and clients reach the device_gateway through the API's own address.

Two real servers, as deployed: the API router (forwarding) and the
device_gateway app, with real PostgreSQL 17 + PostgREST. A simulated device
connects to the API; its socket, a file request, a tunnel and a revocation all
cross the API worker to the gateway unchanged.
"""

import asyncio
import base64
import socket as socket_module
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
import uvicorn
import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

import src.platform.auth.dependencies as auth_dependencies
import src.platform.hosts.gateway_service as gateway_service
import src.platform.hosts.router as hosts_router
from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.hosts.connection_auth import HostConnectionAuth, device_request_message, proof_message
from src.platform.hosts.gateway import HostGateway
from src.platform.hosts.router import get_connection_auth, get_host_service, require_host_devices_enabled, router
from src.platform.hosts.service import HostService
from tests.platform.test_host_devices_native import database, repository, users  # noqa: F401  (fixtures)

SECRET = "f" * 48


def free_port():
    with socket_module.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="on", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline and thread.is_alive(), "server did not start"
        time.sleep(0.05)
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)


@pytest.fixture
def deployed(database, repository, monkeypatch):
    owner = str(uuid4())
    users(database, owner)
    gateway_port, api_port = free_port(), free_port()
    monkeypatch.setattr(settings, "HOST_DEVICES_ENABLED", True)
    monkeypatch.setattr(settings, "HOST_GATEWAY_INTERNAL_URL", f"http://127.0.0.1:{gateway_port}")
    monkeypatch.setattr(settings, "HOST_GATEWAY_INTERNAL_SECRET", SECRET)
    monkeypatch.setattr(settings, "HOST_GATEWAY_PUBLIC_URL", "")
    monkeypatch.setattr(gateway_service, "_gateway", HostGateway(repository))

    class Auth:
        def get_current_user(self, token):
            if token != "owner-token":
                raise ValueError("bad token")
            return CurrentUser(user_id=owner, role="authenticated")

    monkeypatch.setattr(auth_dependencies, "get_auth_service", lambda: Auth())
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[require_host_devices_enabled] = lambda: None
    api.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=owner, role="authenticated")
    api.dependency_overrides[get_host_service] = lambda: HostService(repository)
    api.dependency_overrides[get_connection_auth] = lambda: HostConnectionAuth(repository)
    # The API must not hold device sockets itself.
    monkeypatch.setattr(hosts_router, "_gateway", None)
    with serve(gateway_service.app, gateway_port), serve(api, api_port) as address:
        yield {"owner": owner, "api": address}


def test_device_sockets_files_tunnels_and_revocation_cross_the_api_to_the_gateway(deployed):
    owner, address = deployed["owner"], deployed["api"]
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    http = httpx.Client(base_url=f"http://{address}/api/v1", trust_env=False, timeout=30)
    device = http.post("/hosts/devices", json={"public_key": base64.b64encode(raw).decode(), "name": "Mac",
                                               "platform": "darwin"}).json()["device_id"]
    folder = str(uuid4())
    assert http.post(f"/hosts/devices/{device}/folders",
                     json={"folder_id": folder, "name": "Notes"}).status_code == 201

    async def scenario():
        timestamp = int(time.time())
        challenge = http.post(f"/hosts/devices/{device}/device-challenge", json={
            "owner": owner, "timestamp": timestamp,
            "signature": base64.b64encode(key.sign(device_request_message(owner, device, timestamp))).decode()}).json()
        # No gateway address is handed out: the device dials the API.
        assert "connect_url" not in challenge
        proof = key.sign(proof_message(owner, device, challenge["challenge_id"], challenge["epoch"], challenge["nonce"]))
        async with websockets.connect(f"ws://{address}/api/v1/hosts/devices/{device}/connect", proxy=None) as host:
            await host.send(__import__("json").dumps({
                "owner": owner, "challenge_id": challenge["challenge_id"], "epoch": challenge["epoch"],
                "nonce": challenge["nonce"], "signature": base64.b64encode(proof).decode(),
                "protocol": 1, "boot_id": str(uuid4()), "app_version": "1.4.0"}))
            assert __import__("json").loads(await host.recv()) == {"type": "ready", "epoch": challenge["epoch"]}

            # A file request: API worker -> gateway (internal) -> device socket (forwarded) -> back.
            async def serve_file():
                frame = __import__("json").loads(await host.recv())
                assert frame["type"] == "file" and frame["folder_id"] == folder
                await host.send(__import__("json").dumps({"type": "reply", "id": frame["id"],
                    "result": {"content_base64": base64.b64encode(b"hello").decode(), "revision": "a" * 64}}))

            reply, read = await asyncio.gather(serve_file(), asyncio.to_thread(
                http.get, f"/hosts/folders/{folder}/file", params={"path": "note.md"}))
            assert read.status_code == 200, read.text
            assert base64.b64decode(read.json()["content_base64"]) == b"hello"

            # A tunnel: bytes cross both forwarded sockets untouched, as binary.
            async with websockets.connect(f"ws://{address}/api/v1/hosts/folders/{folder}/tunnel",
                                          subprotocols=["version.v1", "version.bearer.owner-token"], proxy=None) as tunnel:
                opened = __import__("json").loads(await host.recv())
                assert opened["type"] == "tunnel_open" and opened["folder_id"] == folder
                await host.send(__import__("json").dumps({"type": "tunnel_opened", "stream": opened["stream"]}))
                assert __import__("json").loads(await tunnel.recv()) == {"type": "ready"}
                await tunnel.send(b"\x00SSH-2.0-client\xff")
                data = __import__("json").loads(await host.recv())
                assert base64.b64decode(data["data"]) == b"\x00SSH-2.0-client\xff"
                await host.send(__import__("json").dumps({"type": "tunnel_data", "stream": opened["stream"],
                                                          "data": base64.b64encode(b"SSH-2.0-host").decode()}))
                assert await tunnel.recv() == b"SSH-2.0-host"

            # Revocation through the API closes the device with 4004, which survives the hop.
            assert (await asyncio.to_thread(http.delete, f"/hosts/devices/{device}")).json() == {"revoked": True}
            with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
                while True:
                    await host.recv()
            assert closed.value.rcvd.code == 4004

    asyncio.run(scenario())
