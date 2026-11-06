"""The device gateway carries tunnels and heartbeats only; the plaintext relay is gone."""

from uuid import UUID

import pytest
from fastapi import HTTPException

from src.platform.hosts import gateway as gateway_module
from src.platform.hosts.gateway import CLOSE_INVALID, GATEWAY_INSTANCE, DeviceConnection, HostGateway

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
FOLDER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
EPOCH = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


class Repository:
    def __init__(self):
        self.calls = []

    def command(self, name, **params):
        self.calls.append(name)
        if name == "host_folder_admit":
            if params["p_actor"] != OWNER:
                raise ValueError("denied")
            return {"device_id": str(DEVICE), "epoch": str(EPOCH), "gateway_instance": GATEWAY_INSTANCE}
        return True


class Authentication:
    def accept(self, *args, **kwargs):
        return True


class DeviceSocket:
    """A device that proves itself, then sends `frames`."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = None

    async def accept(self):
        pass

    async def receive_json(self):
        if not self.frames:
            raise gateway_module.WebSocketDisconnect()
        return self.frames.pop(0)

    async def send_json(self, frame):
        self.sent.append(frame)

    async def close(self, code=1000):
        self.closed = code


PROOF = {"owner": OWNER, "challenge_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "epoch": str(EPOCH), "nonce": "n",
         "signature": "s", "protocol": 1, "boot_id": "99999999-9999-4999-8999-999999999999", "app_version": "1.0.0"}


@pytest.mark.asyncio
@pytest.mark.parametrize("frame", [
    {"type": "reply", "id": "ffffffff-ffff-4fff-8fff-ffffffffffff", "result": {"content_base64": "c2VjcmV0"}},
    {"type": "event", "subscription": "s", "paths": ["a.md"]},
    {"type": "exec_output", "session": "s", "data": "eA=="},
])
async def test_a_device_that_sends_plaintext_relay_frames_is_closed(frame):
    socket = DeviceSocket([PROOF, {"type": "heartbeat"}, frame])
    gateway = HostGateway(repository=Repository(), authentication=Authentication())
    await gateway.connected(DEVICE, socket)
    assert socket.sent[0] == {"type": "ready", "epoch": str(EPOCH)}
    assert {"type": "heartbeat_ack"} in socket.sent
    assert socket.closed == CLOSE_INVALID
    assert DEVICE not in gateway.connections


@pytest.mark.asyncio
async def test_tunnels_are_admitted_for_the_owner_on_the_current_connection_only():
    gateway = HostGateway(repository=Repository(), authentication=Authentication())
    connection = DeviceConnection(DEVICE, EPOCH, DeviceSocket([]))
    gateway.connections[DEVICE] = connection
    admitted = await gateway._admit(OWNER, FOLDER, "read")
    assert gateway._connection(admitted) is connection
    with pytest.raises(HTTPException) as denied:
        await gateway._admit("ffffffff-ffff-4fff-8fff-ffffffffffff", FOLDER, "read")
    assert denied.value.status_code == 403
    connection.closed = True
    with pytest.raises(HTTPException) as offline:
        gateway._connection(admitted)
    assert offline.value.detail == {"code": "device_offline"}


def test_the_relay_is_retired():
    for name in ("dispatch", "watch", "exec"):
        assert not hasattr(HostGateway, name), name
    assert not hasattr(DeviceConnection, "pending")
