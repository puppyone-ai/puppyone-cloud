"""One computer opens a folder on another: real SSH, end to end, through the real API and gateway.

Real PostgreSQL 17 + PostgREST, the API and the device_gateway as two HTTP
servers. The device dials the API with its signed proof; its SSH server
(asyncssh here, the Desktop's host service in production) trusts the Cloud's
certificate authority and identifies itself with the device key. The client
gets a five-minute certificate from the API, pins the device's registered host
key and talks SSH through the tunnel. Proven here: the session works, its
content never appears in what the gateway relays, and a wrong host key or a
certificate for another computer is refused.
"""

import asyncio
import base64
import json
import socket
import time
from uuid import uuid4

import asyncssh
import httpx
import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.platform.hosts.connection_auth import device_request_message, proof_message

MARKER = "folder-content-" + uuid4().hex


def register(http, name="Mac"):
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    device = http.post("/hosts/devices", json={"public_key": base64.b64encode(raw).decode(), "name": name,
                                               "platform": "linux"}).json()["device_id"]
    folder = str(uuid4())
    assert http.post(f"/hosts/devices/{device}/folders", json={"folder_id": folder, "name": "Notes",
                                                                "writable": True}).status_code == 201
    return key, device, folder


async def online(http, address, owner, device, key):
    timestamp = int(time.time())
    challenge = http.post(f"/hosts/devices/{device}/device-challenge", json={
        "owner": owner, "timestamp": timestamp,
        "signature": base64.b64encode(key.sign(device_request_message(owner, device, timestamp))).decode()}).json()
    proof = key.sign(proof_message(owner, device, challenge["challenge_id"], challenge["epoch"], challenge["nonce"]))
    socket_ = await websockets.connect(f"ws://{address}/api/v1/hosts/devices/{device}/connect", proxy=None)
    await socket_.send(json.dumps({
        "owner": owner, "challenge_id": challenge["challenge_id"], "epoch": challenge["epoch"],
        "nonce": challenge["nonce"], "signature": base64.b64encode(proof).decode(),
        "protocol": 1, "boot_id": str(uuid4()), "app_version": "1.4.0"}))
    assert json.loads(await socket_.recv())["type"] == "ready"
    return socket_


class Device:
    """The sharing computer: serves each tunnel with an SSH server and records every relayed byte."""

    def __init__(self, socket_, key, device, authority):
        self.socket, self.device = socket_, device
        self.host_key = asyncssh.import_private_key(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        # Accept only the Cloud authority's certificates for this device.
        self.authorized = asyncssh.import_authorized_keys(f'cert-authority,principals="{device}" {authority}\n')
        self.streams, self.relayed, self.logins = {}, [], []
        # asyncio keeps only weak references to tasks: hold the SSH servers and pumps until the end.
        self.tasks, self.connections, self.pumps = set(), [], {}

    async def serve(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                frame = json.loads(await self.socket.recv())
            except websockets.ConnectionClosed:
                return
            stream = frame.get("stream")
            if frame["type"] == "tunnel_open":
                ours, theirs = socket.socketpair()
                ours.setblocking(False)
                self.streams[stream] = ours
                await self.socket.send(json.dumps({"type": "tunnel_opened", "stream": stream}))
                self.tasks.add(asyncio.create_task(self.accept(theirs)))
                self.pumps[stream] = asyncio.create_task(self.pump(stream, ours, loop))
            elif frame["type"] == "tunnel_data" and stream in self.streams:
                data = base64.b64decode(frame["data"])
                self.relayed.append(data)
                await loop.sock_sendall(self.streams[stream], data)
            elif frame["type"] == "tunnel_close" and stream in self.streams:
                await self.close(stream)

    async def close(self, stream):
        # Stop reading before closing: a closed descriptor is reused by the next tunnel's socket.
        pump = self.pumps.pop(stream, None)
        if pump:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        self.streams.pop(stream).close()

    async def accept(self, sock):
        # run_server returns an awaitable, not a coroutine: await it inside one.
        self.connections.append(await asyncssh.run_server(
            sock, server_host_keys=[self.host_key], authorized_client_keys=self.authorized,
            process_factory=self.run, login_timeout=30))

    async def pump(self, stream, ours, loop):
        while True:
            try:
                data = await loop.sock_recv(ours, 65536)
            except OSError:
                return
            if not data:
                await self.socket.send(json.dumps({"type": "tunnel_close", "stream": stream}))
                return
            self.relayed.append(data)
            await self.socket.send(json.dumps({"type": "tunnel_data", "stream": stream,
                                               "data": base64.b64encode(data).decode()}))

    async def run(self, process):
        self.logins.append(process.get_extra_info("username"))
        process.stdout.write(f"{MARKER} ran `{process.command}` for {process.get_extra_info('username')}\n")
        process.exit(7)


async def ssh(address, http, device, folder, *, host_key=None, certificate_for=None):
    """The accessing computer: a fresh key, a certificate from the API, the device's pinned host key."""
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    public_key = client_key.export_public_key("openssh").decode().strip()

    def certificate(target):
        issued = http.post(f"/hosts/devices/{target}/ssh-certificate", json={"public_key": public_key})
        assert issued.status_code == 200, issued.text
        return issued.json()

    issued = certificate(certificate_for or device)
    # The host key always comes from the computer being opened.
    pinned = asyncssh.import_public_key(host_key or (certificate(device) if certificate_for else issued)["host_key"])
    tunnel = await websockets.connect(f"ws://{address}/api/v1/hosts/folders/{folder}/tunnel",
                                      subprotocols=["version.v1", "version.bearer.owner-token"], proxy=None)
    assert json.loads(await tunnel.recv()) == {"type": "ready"}
    ours, theirs = socket.socketpair()
    ours.setblocking(False)
    loop = asyncio.get_running_loop()

    async def upstream():
        while data := await loop.sock_recv(ours, 65536):
            await tunnel.send(data)

    async def downstream():
        async for message in tunnel:
            await loop.sock_sendall(ours, message)

    pumps = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
    try:
        async with asyncssh.run_client(theirs, config=None, host=f"{device}.devices.puppyone", username=folder,
                                       client_keys=[(client_key, asyncssh.import_certificate(issued["certificate"]))],
                                       known_hosts=([pinned], [], []), preferred_auth="publickey",
                                       login_timeout=30) as connection:
            return await connection.run("echo $((6*7))", check=False)
    finally:
        for pump in pumps:
            pump.cancel()
        # Stop reading before closing: a closed descriptor is reused by the next socket.
        await asyncio.gather(*pumps, return_exceptions=True)
        await tunnel.close()
        ours.close()


def test_one_computer_opens_another_computers_folder_over_ssh_end_to_end(deployed):
    owner, address = deployed["owner"], deployed["api"]
    http = httpx.Client(base_url=f"http://{address}/api/v1", trust_env=False, timeout=30)
    authority = http.get("/hosts/ssh-authority").json()["keys"][0]
    key, device, folder = register(http)
    other_key, other_device, other_folder = register(http, "PC")

    async def scenario():
        sharing = Device(await online(http, address, owner, device, key), key, device, authority)
        serving = asyncio.create_task(sharing.serve())
        try:
            result = await asyncio.wait_for(ssh(address, http, device, folder), 60)
            assert result.exit_status == 7
            assert result.stdout == f"{MARKER} ran `echo $((6*7))` for {folder}\n"
            assert sharing.logins == [folder]
            # The gateway relayed the session, but none of it in the clear.
            relayed = b"".join(sharing.relayed)
            assert relayed.startswith(b"SSH-2.0-") and MARKER.encode() not in relayed
            assert b"echo $((6*7))" not in relayed

            # Any other host key is refused, even through the right tunnel.
            impostor = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode()
            try:
                await asyncio.wait_for(ssh(address, http, device, folder, host_key=impostor), 60)
                raise AssertionError("an unpinned host key was accepted")
            except asyncssh.HostKeyNotVerifiable:
                pass

            # A certificate for the owner's other computer does not open this one.
            try:
                await asyncio.wait_for(ssh(address, http, device, folder, certificate_for=other_device), 60)
                raise AssertionError("a certificate for another computer was accepted")
            except asyncssh.PermissionDenied:
                pass
            assert sharing.logins == [folder]
            assert other_key and other_folder
        finally:
            await sharing.socket.close()
            serving.cancel()
            for task in [*sharing.tasks, *sharing.pumps.values()]:
                task.cancel()

    asyncio.run(scenario())
