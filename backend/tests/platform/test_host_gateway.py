import asyncio
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
        self.fail_audit = None

    def command(self, name, **params):
        if name == "host_file_event_record":
            if params["p_outcome"] == self.fail_audit:
                raise RuntimeError("audit unavailable")
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


class WatchSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, frame):
        self.sent.append(frame)


@pytest.mark.asyncio
async def test_watch_fans_out_isolates_slow_readers_and_ends_on_disconnect(monkeypatch):
    import src.platform.hosts.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "SUBSCRIBER_QUEUE", 2)
    repository = Repository()
    gateway = HostGateway(repository, authentication=object())
    socket = WatchSocket()
    connection = DeviceConnection(DEVICE, EPOCH, socket)
    gateway.connections[DEVICE] = connection
    fast, slow = gateway.watch(OWNER, FOLDER), gateway.watch(OWNER, FOLDER)
    assert await anext(fast) == {"paths": [], "reset": True}
    assert await anext(slow) == {"paths": [], "reset": True}
    # One device-side watcher serves every reader of the folder.
    assert [frame["type"] for frame in socket.sent] == ["watch"]
    subscription = socket.sent[0]["subscription"]
    for index in range(5):
        gateway._fan_out(connection, {"type": "event", "subscription": subscription,
                                      "paths": [f"note-{index}.md"], "reset": False})
        assert (await anext(fast))["paths"] == [f"note-{index}.md"]
    # The slow reader lost its backlog and is told to rescan, never blocking the device.
    assert await anext(slow) == {"paths": [], "reset": True}
    gateway._fan_out(connection, {"type": "event", "subscription": subscription, "paths": ["x" * 5000]})
    assert await anext(fast) == {"paths": [], "reset": True}
    await slow.aclose()
    assert [frame["type"] for frame in socket.sent] == ["watch"]
    await fast.aclose()
    assert socket.sent[-1] == {"type": "unwatch", "subscription": subscription}

    reader = gateway.watch(OWNER, FOLDER)
    assert (await anext(reader))["reset"] is True
    for watch in connection.watches.values():
        for subscriber in watch.subscribers:
            subscriber.offer(gateway_module._CLOSED)
    assert await anext(reader) == {"unavailable": {"retryable": True}}
    assert repository.events.count("intended") == 2 and repository.events.count("succeeded") == 2


@pytest.mark.asyncio
async def test_unknown_actions_and_device_errors_never_leak_detail():
    repository = Repository()
    gateway = HostGateway(repository, authentication=object())

    class ErrorSocket(Socket):
        async def send_json(self, frame):
            self.sent.append(frame)
            self.connection.pending[frame["id"]].set_result(
                {"type": "reply", "id": frame["id"], "error": "/Users/private/secret.md"})

    socket = ErrorSocket()
    connection = DeviceConnection(DEVICE, EPOCH, socket)
    socket.connection = connection
    gateway.connections[DEVICE] = connection
    with pytest.raises(HTTPException) as unknown:
        await gateway.dispatch(OWNER, FOLDER, "chmod", {"path": "note.md"})
    assert unknown.value.status_code == 400 and socket.sent == []
    with pytest.raises(HTTPException) as opaque:
        await gateway.dispatch(OWNER, FOLDER, "read", {"path": "note.md"})
    assert opaque.value.detail == {"code": "file_operation_failed"}


@pytest.mark.asyncio
async def test_audit_failure_is_closed_before_delivery_and_unknown_after_write():
    repository = Repository()
    gateway = HostGateway(repository, authentication=object())
    socket = Socket()
    connection = DeviceConnection(DEVICE, EPOCH, socket)
    socket.connection = connection
    gateway.connections[DEVICE] = connection
    repository.fail_audit = "intended"
    with pytest.raises(HTTPException) as rejected:
        await gateway.dispatch(OWNER, FOLDER, "write", {"path": "note.md"})
    assert rejected.value.status_code == 503
    assert rejected.value.detail["outcome_unknown"] is False
    assert socket.sent == []
    repository.fail_audit = "succeeded"
    with pytest.raises(HTTPException) as uncertain:
        await gateway.dispatch(OWNER, FOLDER, "write", {"path": "note.md"})
    assert uncertain.value.status_code == 503
    assert uncertain.value.detail["outcome_unknown"] is True
    assert repository.events == ["intended"]
    assert len(socket.sent) == 1


@pytest.mark.asyncio
async def test_git_names_an_operation_and_writes_are_never_assumed_done():
    repository = Repository()
    gateway = HostGateway(repository, authentication=object())

    class GitSocket(Socket):
        async def send_json(self, frame):
            self.sent.append(frame)

    socket = GitSocket()
    connection = DeviceConnection(DEVICE, EPOCH, socket)
    socket.connection = connection
    gateway.connections[DEVICE] = connection
    with pytest.raises(HTTPException) as unknown:
        await gateway.dispatch(OWNER, FOLDER, "git", {"op": "config", "args": ["--global"]})
    assert unknown.value.status_code == 400 and socket.sent == []
    repository.fail_audit = "unknown"
    task = asyncio.create_task(gateway.dispatch(OWNER, FOLDER, "git", {"op": "commit", "message": "m"}))
    for _ in range(200):  # Admission runs in a worker thread first.
        if socket.sent:
            break
        await asyncio.sleep(0.01)
    frame = socket.sent[-1]
    assert (frame["action"], frame["op"]) == ("git", "commit")
    connection.pending[frame["id"]].set_exception(HTTPException(503, {"code": "device_disconnected"}))
    with pytest.raises(HTTPException) as lost:
        await task
    # The audit of a lost commit failed too: the client must not assume either way.
    assert lost.value.detail == {"code": "audit_unavailable", "outcome_unknown": True}
