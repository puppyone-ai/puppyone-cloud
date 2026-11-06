"""Tunnel frames from the device: bytes only, bounded, and a slow client never stalls the device socket."""

import asyncio
import base64
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.platform.hosts.tunnel import (
    MAX_CHUNK,
    MAX_QUEUED_BYTES,
    TUNNEL_REFUSALS,
    Tunnel,
    device_tunnel_frame,
    end_tunnels,
)

FOLDER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def tunnel(stream="t1"):
    return Tunnel(stream, FOLDER, asyncio.get_running_loop().create_future())


def connection(*tunnels):
    return SimpleNamespace(tunnels={item.id: item for item in tunnels})


@pytest.mark.asyncio
async def test_device_bytes_reach_the_client_in_order_and_the_end_is_delivered_once():
    item = tunnel()
    link = connection(item)
    assert device_tunnel_frame(link, {"type": "tunnel_opened", "stream": "t1"}) is True
    assert item.opened.result() is None
    for chunk in (b"\x00SSH", b"-2.0\xff"):
        device_tunnel_frame(link, {"type": "tunnel_data", "stream": "t1", "data": base64.b64encode(chunk).decode()})
    device_tunnel_frame(link, {"type": "tunnel_close", "stream": "t1"})
    received = [item.queue.get_nowait() for _ in range(item.queue.qsize())]
    assert received == [b"\x00SSH", b"-2.0\xff", None]
    assert item.ended_by_device is True
    # Anything after the end is dropped.
    item.deliver(b"late")
    assert item.queue.empty()


@pytest.mark.asyncio
async def test_a_refusal_carries_a_known_code_only():
    known, unknown = tunnel("a"), tunnel("b")
    link = connection(known, unknown)
    device_tunnel_frame(link, {"type": "tunnel_refused", "stream": "a", "error": "not_allowed"})
    device_tunnel_frame(link, {"type": "tunnel_refused", "stream": "b", "error": "/Users/someone/private: denied"})
    assert known.opened.result() == "not_allowed" and "not_allowed" in TUNNEL_REFUSALS
    assert unknown.opened.result() == "tunnel_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["%%%not-base64", 7, base64.b64encode(b"x" * (2 * MAX_CHUNK)).decode()])
async def test_malformed_or_oversized_data_is_a_protocol_error(data):
    link = connection(tunnel())
    with pytest.raises(ValueError):
        device_tunnel_frame(link, {"type": "tunnel_data", "stream": "t1", "data": data})


@pytest.mark.asyncio
async def test_late_frames_and_other_frames_never_disturb_a_tunnel():
    link = connection(tunnel())
    assert device_tunnel_frame(link, {"type": "tunnel_data", "stream": "gone", "data": "eA=="}) is True
    assert device_tunnel_frame(link, {"type": "tunnel_data", "stream": 5, "data": "eA=="}) is True
    assert device_tunnel_frame(link, {"type": "heartbeat"}) is False
    assert link.tunnels["t1"].queue.empty()


@pytest.mark.asyncio
async def test_a_client_that_falls_behind_loses_its_tunnel_not_the_device_socket():
    item = tunnel()
    item.deliver(b"x" * MAX_QUEUED_BYTES)
    assert not item.closed
    item.deliver(b"y")
    assert item.closed is True
    queued = [item.queue.get_nowait() for _ in range(item.queue.qsize())]
    assert queued[-1] is None


@pytest.mark.asyncio
async def test_the_device_going_away_ends_every_tunnel_including_those_not_yet_open():
    waiting, open_ = tunnel("a"), tunnel("b")
    open_.opened.set_result(None)
    link = connection(waiting, open_)
    end_tunnels(link)
    assert waiting.opened.result() == "tunnel_unavailable"
    assert link.tunnels == {}
    for item in (waiting, open_):
        assert item.queue.get_nowait() is None
