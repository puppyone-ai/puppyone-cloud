"""Devices and clients reach the device_gateway through the API's own address.

Two real servers, as deployed: the API router (forwarding) and the
device_gateway app, with real PostgreSQL 17 + PostgREST. A simulated device
connects to the API; its socket, a file request, a tunnel and a revocation all
cross the API worker to the gateway unchanged.
"""

import asyncio
import base64
import time
from uuid import uuid4

import httpx
import pytest
import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.platform.hosts.connection_auth import (
    device_request_message,
    proof_message,
)


def test_device_sockets_tunnels_and_revocation_cross_the_api_to_the_gateway(deployed):
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

            # No plaintext file route: content crosses only as tunnel bytes.
            missing = await asyncio.to_thread(http.get, f"/hosts/folders/{folder}/file", params={"path": "note.md"})
            assert missing.status_code == 404

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
