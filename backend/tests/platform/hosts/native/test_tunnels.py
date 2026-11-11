"""SSH certificates and byte tunnels on real PostgreSQL 17 + PostgREST.

The gateway admits a tunnel like a folder request and then relays bytes it
does not interpret; the SSH session inside ends on the host.
"""

import base64
import time
from contextlib import ExitStack
from uuid import uuid4

import asyncssh
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.websockets import WebSocketDisconnect

import src.platform.auth.dependencies as auth_dependencies
import src.platform.hosts.router as hosts_router
from src.platform.auth.models import CurrentUser
from src.platform.hosts.ssh_authority import HostSshAuthority
from tests.platform.hosts.native.support import _exit, connect, users


@pytest.fixture
def scene(database, api, monkeypatch):
    client, actor = api
    owner, stranger = str(uuid4()), str(uuid4())
    users(database, owner, stranger)
    authority = HostSshAuthority()
    monkeypatch.setattr(hosts_router, "host_ssh_authority", lambda: authority)

    class Auth:
        def get_current_user(self, token):
            identities = {"owner-token": owner, "stranger-token": stranger}
            if token not in identities:
                raise ValueError("bad token")
            return CurrentUser(user_id=identities[token], role="authenticated")

    monkeypatch.setattr(auth_dependencies, "get_auth_service", lambda: Auth())
    actor["id"] = owner
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    device = client.post("/api/v1/hosts/devices", json={"public_key": base64.b64encode(raw).decode(),
                                                       "name": "Mac", "platform": "darwin"}).json()["device_id"]
    folder = str(uuid4())
    assert client.post(f"/api/v1/hosts/devices/{device}/folders",
                       json={"folder_id": folder, "name": "Notes"}).status_code == 201
    return {"client": client, "actor": actor, "owner": owner, "stranger": stranger, "key": key, "raw": raw,
            "device": device, "folder": folder, "authority": authority}


def test_certificates_are_short_lived_and_only_for_the_owners_own_device(scene):
    client, actor, device = scene["client"], scene["actor"], scene["device"]
    [ca_text] = client.get("/api/v1/hosts/ssh-authority").json()["keys"]
    user_key = asyncssh.generate_private_key("ssh-ed25519")
    user_public = user_key.export_public_key("openssh").decode().strip()
    issued = client.post(f"/api/v1/hosts/devices/{device}/ssh-certificate", json={"public_key": user_public})
    assert issued.status_code == 200, issued.text
    body = issued.json()
    certificate = asyncssh.import_certificate(body["certificate"])
    assert certificate.principals == [device]
    assert certificate.signing_key.export_public_key("openssh").decode().strip() == ca_text.strip()
    assert certificate.key.export_public_key("openssh").decode().strip() == user_public
    assert 0 < certificate._valid_before - time.time() <= 300
    assert body["valid_before"] == certificate._valid_before
    # The host key a client must see is the device's registered key.
    assert asyncssh.import_public_key(body["host_key"]).public_data[-32:] == scene["raw"]

    assert client.post(f"/api/v1/hosts/devices/{device}/ssh-certificate",
                       json={"public_key": "ssh-rsa " + "A" * 80}).status_code == 422
    actor["id"] = scene["stranger"]
    assert client.post(f"/api/v1/hosts/devices/{device}/ssh-certificate",
                       json={"public_key": user_public}).status_code == 404
    actor["id"] = scene["owner"]
    assert client.delete(f"/api/v1/hosts/devices/{device}").status_code == 200
    assert client.post(f"/api/v1/hosts/devices/{device}/ssh-certificate",
                       json={"public_key": user_public}).status_code == 404


def test_tunnels_relay_bytes_only_for_the_owner_and_record_only_that_they_happened(database, scene):
    client, device, folder = scene["client"], scene["device"], scene["folder"]
    path = f"/api/v1/hosts/folders/{folder}/tunnel"
    with ExitStack() as sockets:
        with client.websocket_connect(path, subprotocols=["version.bearer.owner-token"]) as offline:
            assert offline.receive_json() == {"type": "error", "code": "device_offline"}
        socket = connect(client, sockets, scene["owner"], device, scene["key"])
        with client.websocket_connect(path, subprotocols=["version.bearer.stranger-token"]) as denied:
            assert denied.receive_json() == {"type": "error", "code": "folder_unavailable"}
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, subprotocols=["version.bearer.nobody"]) as anonymous:
                anonymous.receive_json()

        session = client.websocket_connect(path, subprotocols=["version.bearer.owner-token"])
        tunnel = session.__enter__()
        sockets.callback(_exit, session)
        opened = socket.receive_json()
        assert opened["type"] == "tunnel_open" and opened["folder_id"] == folder
        stream = opened["stream"]
        socket.send_json({"type": "tunnel_opened", "stream": stream})
        assert tunnel.receive_json() == {"type": "ready"}

        # Bytes in both directions, untouched; large writes are chunked for the device socket.
        payload = bytes(range(256)) * 600
        tunnel.send_bytes(payload)
        received = b""
        while len(received) < len(payload):
            frame = socket.receive_json()
            assert frame["type"] == "tunnel_data" and frame["stream"] == stream
            received += base64.b64decode(frame["data"])
        assert received == payload
        socket.send_json({"type": "tunnel_data", "stream": stream,
                          "data": base64.b64encode(b"SSH-2.0-host").decode()})
        assert tunnel.receive_bytes() == b"SSH-2.0-host"

        # The host ends it: the client learns at once.
        socket.send_json({"type": "tunnel_close", "stream": stream})
        with pytest.raises(WebSocketDisconnect):
            tunnel.receive_bytes()

        # A host that refuses (for example a folder it no longer shares) says why.
        with client.websocket_connect(path, subprotocols=["version.bearer.owner-token"]) as refused:
            again = socket.receive_json()
            socket.send_json({"type": "tunnel_refused", "stream": again["stream"], "error": "folder_unavailable"})
            assert refused.receive_json() == {"type": "error", "code": "folder_unavailable"}

    outcomes = database.sql(
        "SELECT string_agg(outcome, ',' ORDER BY id) FROM public.host_file_events "
        f"WHERE device_id='{device}' AND operation='tunnel';")
    assert outcomes == "intended,succeeded,intended,failed"


def test_a_client_leaving_closes_its_tunnel_on_the_host_and_never_the_device_socket(database, scene):
    client, device, folder = scene["client"], scene["device"], scene["folder"]
    path = f"/api/v1/hosts/folders/{folder}/tunnel"
    with ExitStack() as sockets:
        socket = connect(client, sockets, scene["owner"], device, scene["key"])
        with client.websocket_connect(path, subprotocols=["version.bearer.owner-token"]) as tunnel:
            stream = socket.receive_json()["stream"]
            socket.send_json({"type": "tunnel_opened", "stream": stream})
            assert tunnel.receive_json() == {"type": "ready"}
        assert socket.receive_json() == {"type": "tunnel_close", "stream": stream}
        # The device socket is still the same, serving the next request.
        socket.send_json({"type": "heartbeat"})
        assert socket.receive_json() == {"type": "heartbeat_ack"}


def test_a_tunnel_that_falls_behind_ends_without_buffering_without_limit():
    import asyncio

    from src.platform.hosts.tunnel import MAX_QUEUED_BYTES, Tunnel

    async def scenario():
        tunnel = Tunnel("t", uuid4(), asyncio.get_running_loop().create_future())
        chunk = b"x" * (1024 * 1024)
        for _ in range(MAX_QUEUED_BYTES // len(chunk)):
            tunnel.deliver(chunk)
        assert not tunnel.closed
        tunnel.deliver(chunk)
        assert tunnel.closed
        tunnel.deliver(chunk)  # ignored once closed
        items = []
        while not tunnel.queue.empty():
            items.append(tunnel.queue.get_nowait())
        assert items[-1] is None and len(items) == MAX_QUEUED_BYTES // len(chunk) + 1

    asyncio.run(scenario())
