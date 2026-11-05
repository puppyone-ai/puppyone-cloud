from uuid import UUID

import pytest
from fastapi import HTTPException

from src.platform.hosts.gateway import GATEWAY_INSTANCE, DeviceConnection, HostGateway

OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEVICE = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
FOLDER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
EPOCH = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


class Repository:
    def __init__(self):
        self.epoch = str(EPOCH)
        self.owner = OWNER
        self.events = []

    def command(self, name, **params):
        if name == "host_file_event_record":
            self.events.append(params["p_outcome"])
            return True
        assert name == "host_folder_admit"
        if params["p_actor"] != self.owner:
            raise ValueError("denied")
        return {"device_id": str(DEVICE), "epoch": self.epoch,
                "gateway_instance": GATEWAY_INSTANCE}


class Socket:
    def __init__(self):
        self.connection = None
        self.sent = []

    async def send_json(self, frame):
        self.sent.append(frame)
        if frame["type"] == "file":
            self.connection.pending[frame["id"]].set_result(
                {"type": "reply", "id": frame["id"], "result": {"ok": True}})


@pytest.mark.asyncio
async def test_dispatch_uses_current_epoch_and_never_trusts_client_supplied_folder():
    repository = Repository()
    gateway = HostGateway(repository, authentication=object())
    socket = Socket()
    connection = DeviceConnection(DEVICE, EPOCH, socket)
    socket.connection = connection
    gateway.connections[DEVICE] = connection
    result = await gateway.dispatch(OWNER, FOLDER, "read", {"path": "note.md", "folder_id": "other"})
    assert result == {"ok": True}
    assert repository.events == ["intended", "succeeded"]
    assert socket.sent[0]["folder_id"] == str(FOLDER)
    assert not connection.pending
    repository.epoch = str(UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"))
    with pytest.raises(HTTPException) as stale:
        await gateway.dispatch(OWNER, FOLDER, "read", {"path": "note.md"})
    assert stale.value.status_code == 503
    with pytest.raises(HTTPException) as stranger:
        await gateway.dispatch("ffffffff-ffff-4fff-8fff-ffffffffffff", FOLDER, "read", {"path": "note.md"})
    assert stranger.value.status_code == 403
