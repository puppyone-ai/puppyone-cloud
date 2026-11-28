"""Fakes for the platform adapter tests: an HTTP recorder, a clock, a fake WeCom socket."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx


class FakeClock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


Reply = dict[str, Any] | tuple[int, dict[str, Any], dict[str, str]]


class Calls:
    """An httpx transport that records every request and answers from ``reply``."""

    def __init__(self, reply: Callable[[str, dict[str, Any]], Reply] | None = None):
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.headers: list[httpx.Headers] = []
        self._reply = reply or (lambda method, body: {"ok": True, "ts": "1.1", "result": {}})
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content)
        else:
            body = dict(httpx.QueryParams(request.content.decode()))
        self.requests.append((method, body))
        self.headers.append(request.headers)
        answer = self._reply(method, body)
        if isinstance(answer, tuple):
            status, payload, headers = answer
            return httpx.Response(status, json=payload, headers=headers)
        return httpx.Response(200, json=answer)

    def methods(self) -> list[str]:
        return [method for method, _ in self.requests]

    def of(self, method: str) -> list[dict[str, Any]]:
        return [body for name, body in self.requests if name == method]


class FakeSocket:
    """A WeCom socket: frames pushed with ``push`` are read; sent frames are recorded."""

    def __init__(self, ack: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None):
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._ack = ack if ack is not None else (lambda frame: {"errcode": 0, "errmsg": "ok"})

    async def send(self, raw: str) -> None:
        frame = json.loads(raw)
        self.sent.append(frame)
        answer = self._ack(frame)
        if answer is not None:
            self.push({"headers": {"req_id": frame["headers"]["req_id"]}, **answer})

    def push(self, frame: dict[str, Any]) -> None:
        self._inbox.put_nowait(json.dumps(frame))

    def drop(self) -> None:
        self._inbox.put_nowait(None)

    async def recv(self) -> str:
        item = await self._inbox.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    async def close(self) -> None:
        self.closed = True

    def cmds(self) -> list[str]:
        return [frame["cmd"] for frame in self.sent]


class FakeNetwork:
    """``connect`` hands out the prepared sockets in order, then refuses."""

    def __init__(self, *sockets: FakeSocket):
        self.sockets = list(sockets)
        self.connects = 0

    async def connect(self, url: str) -> FakeSocket:
        self.connects += 1
        if not self.sockets:
            raise ConnectionRefusedError("no more sockets")
        return self.sockets.pop(0)


async def until(condition: Callable[[], bool], tries: int = 3000) -> None:
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition never became true")
