"""The Cloud Computer's screen reaches a viewer only through a one-time ticket.

The renderer never holds a session token, so the authenticated API issues an
HMAC ticket bound to one user, workspace and generation; the WebSocket presents
it once, as a subprotocol. The bridge copies bytes without parsing them and
ends when the viewer leaves, the machine changes or the owner's toggle is off.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.workspace import screen as screen_module
from src.platform.workspace.errors import WorkspaceError
from src.platform.workspace.screen import (
    TICKET_PREFIX,
    ScreenTickets,
    WorkspaceScreen,
    ticket_from_protocols,
)

DESKTOP = {"provider": "docker", "profile": "desktop", "artifact": "image"}


def workspace(**changes):
    row = {
        "id": "w1",
        "user_id": "u1",
        "project_id": "p1",
        "generation": "g1",
        "state": "running",
        "binding": DESKTOP,
        "sandbox_settings": {},
    }
    row.update(changes)
    return row


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class FakeStream:
    def __init__(self, chunks):
        self.chunks, self.written, self.closed = list(chunks), [], False

    async def read(self, limit):
        await asyncio.sleep(0)
        return self.chunks.pop(0) if self.chunks else b""

    async def write(self, data):
        self.written.append(data)

    async def close(self):
        self.closed = True


class FakeSocket:
    def __init__(self, messages):
        self.inbox, self.sent = asyncio.Queue(), []
        for message in messages:
            self.inbox.put_nowait(message)

    async def receive(self):
        return await self.inbox.get()

    async def send_bytes(self, data):
        self.sent.append(data)


def service_for(row, stream=None):
    runtime = SimpleNamespace(
        running_connection=AsyncMock(return_value="connection"),
        open_stream=AsyncMock(return_value=stream),
        aclose=AsyncMock(),
    )
    return SimpleNamespace(
        context=Mock(return_value=({"workbench": row, "revision": {"r": 1}}, None)),
        connection=AsyncMock(),
        channel_factory=Mock(return_value=SimpleNamespace(runtime=runtime)),
        repository=SimpleNamespace(activity=Mock()),
        runtime=runtime,
    )


@pytest.fixture(autouse=True)
def streaming_provider(monkeypatch):
    provider = SimpleNamespace(capabilities=SimpleNamespace(streams=frozenset({"screen"})))
    monkeypatch.setattr(screen_module, "runtime_provider", lambda name: provider)
    return provider


def test_a_ticket_is_redeemed_once_for_its_project_only():
    clock = Clock()
    tickets = ScreenTickets("secret", clock=clock)
    token, expires = tickets.issue(workspace())
    assert expires == int(clock.now) + 60
    with pytest.raises(PermissionError):
        tickets.redeem(token, "another-project")
    claims = tickets.redeem(token, "p1")
    assert (claims["sub"], claims["workbench"], claims["generation"]) == ("u1", "w1", "g1")
    with pytest.raises(PermissionError):
        tickets.redeem(token, "p1")


def test_forged_expired_and_foreign_tickets_fail_alike():
    clock = Clock()
    tickets = ScreenTickets("secret", clock=clock)
    token, _ = tickets.issue(workspace())
    payload, signature = token.split(".")
    for forged in (payload + ".x" + signature[1:], "garbage", payload):
        with pytest.raises(PermissionError):
            tickets.redeem(forged, "p1")
    with pytest.raises(PermissionError):
        ScreenTickets("other-secret", clock=clock).redeem(token, "p1")
    clock.now += 61
    with pytest.raises(PermissionError):
        tickets.redeem(token, "p1")


def test_the_ticket_travels_as_a_subprotocol():
    header = f"puppyone.screen.v1, {TICKET_PREFIX}abc.def"
    assert ticket_from_protocols(header) == "abc.def"
    assert ticket_from_protocols("puppyone.screen.v1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "code", "reason"),
    [
        (workspace(state="paused"), "workbench_not_running", None),
        (workspace(binding={"provider": "docker", "artifact": "x"}), "screen_unavailable", "not_installed"),
        (workspace(sandbox_settings={"screen_stream": False}), "screen_unavailable", "user_disabled"),
    ],
)
async def test_session_refuses_before_issuing(row, code, reason):
    screen = WorkspaceScreen(service_for(row), ScreenTickets("secret"))
    with pytest.raises(WorkspaceError) as error:
        await screen.session("u1", "p1")
    assert error.value.detail["code"] == code
    assert error.value.detail.get("reason") == reason


@pytest.mark.asyncio
async def test_session_requires_a_streaming_provider(streaming_provider):
    streaming_provider.capabilities.streams = frozenset()
    screen = WorkspaceScreen(service_for(workspace()), ScreenTickets("secret"))
    with pytest.raises(WorkspaceError) as error:
        await screen.session("u1", "p1")
    assert error.value.detail == {"code": "screen_unavailable", "reason": "unsupported"}


@pytest.mark.asyncio
async def test_session_checks_the_running_image_and_issues_a_ticket():
    service = service_for(workspace())
    screen = WorkspaceScreen(service, ScreenTickets("secret"))
    session = await screen.session("u1", "p1")
    service.connection.assert_awaited_once()
    assert service.connection.await_args.kwargs == {"capabilities": {"screen_stream"}}
    assert session["transport"] == "vnc" and session["protocol"] == "puppyone.screen.v1"
    row, _ = await screen.admit("p1", session["ticket"])
    assert row["id"] == "w1"


@pytest.mark.asyncio
async def test_a_replaced_computer_invalidates_its_ticket():
    service = service_for(workspace())
    screen = WorkspaceScreen(service, ScreenTickets("secret"))
    session = await screen.session("u1", "p1")
    service.context.return_value = ({"workbench": workspace(generation="g2"), "revision": {}}, None)
    with pytest.raises(WorkspaceError) as error:
        await screen.admit("p1", session["ticket"])
    assert error.value.detail["code"] == "workbench_generation_changed"


@pytest.mark.asyncio
async def test_bridge_copies_bytes_both_ways_and_releases_the_stream():
    stream = FakeStream([b"RFB 003.008\n", b"frame"])
    service = service_for(workspace(), stream)
    socket = FakeSocket([{"type": "websocket.receive", "bytes": b"RFB 003.008\n"}])
    screen = WorkspaceScreen(service, ScreenTickets("secret"))
    await screen.bridge(socket, workspace(), {"revision": {}})
    assert socket.sent == [b"RFB 003.008\n", b"frame"]
    assert stream.closed
    service.runtime.open_stream.assert_awaited_once()
    assert service.runtime.open_stream.await_args.args[1] == "screen"
    service.runtime.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_viewer_leaving_ends_the_bridge():
    class Endless(FakeStream):
        async def read(self, limit):
            await asyncio.sleep(3600)

    stream = Endless([])
    socket = FakeSocket([{"type": "websocket.disconnect"}])
    screen = WorkspaceScreen(service_for(workspace(), stream), ScreenTickets("secret"))
    await asyncio.wait_for(screen.bridge(socket, workspace(), {"revision": {}}), 5)
    assert stream.closed
