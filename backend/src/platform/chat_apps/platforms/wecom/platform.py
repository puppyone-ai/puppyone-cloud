"""WeCom intelligent bot over the long connection (``wss://openws.work.weixin.qq.com``).

We connect out, subscribe with Bot ID and Secret, and keep one socket per bot.
Replies go through the socket held by this process; frames for one ``req_id``
are sent one at a time and each waits for its ACK.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from ...ports import (
    Inbound,
    Message,
    NotHere,
    NotVerified,
    Pacing,
    PlatformError,
    RateLimited,
    Sent,
    Target,
)
from .._common import Clock, TokenBucket, fit

log = logging.getLogger(__name__)

_TEXT_BYTES = 20_480
_STREAM_SECONDS = 9 * 60
_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_LEADING_MENTION = re.compile(r"^\s*@\S+\s*")
_MEDIA = {"image", "file", "voice", "video"}
_BACKOFF = (2.0, 5.0, 10.0, 30.0, 60.0)

Connect = Callable[[str], Awaitable[Any]]
Sleep = Callable[[float], Awaitable[None]]


class _Socket:
    """The long connection over aiohttp.

    WeCom's server does not send every frame the strict RFC 6455 way (its
    control pings arrive masked), and ``websockets`` closes the socket on them
    about a minute and a half in; aiohttp reads them. Errors surface as
    ``ConnectionError`` so the session treats every drop alike.
    """

    def __init__(self, session: aiohttp.ClientSession, ws: aiohttp.ClientWebSocketResponse):
        self._session = session
        self._ws = ws

    async def send(self, text: str) -> None:
        try:
            await self._ws.send_str(text)
        except (aiohttp.ClientError, RuntimeError) as exc:
            raise ConnectionError(f"wecom send failed: {type(exc).__name__}") from None

    async def recv(self) -> str:
        message = await self._ws.receive()
        if message.type == aiohttp.WSMsgType.TEXT:
            return message.data
        if message.type == aiohttp.WSMsgType.BINARY:
            return message.data.decode("utf-8", "replace")
        raise ConnectionError(f"wecom connection closed ({message.type.name})")

    async def close(self) -> None:
        try:
            await self._ws.close()
        finally:
            await self._session.close()


async def _connect(url: str) -> _Socket:
    # Direct connection, as in production: a local HTTP proxy is not for this socket.
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(sock_connect=15))
    try:
        ws = await session.ws_connect(url, autoping=True, heartbeat=None)
    except BaseException:
        await session.close()
        raise
    return _Socket(session, ws)


class _Session:
    """One live, subscribed socket."""

    def __init__(self, conn: Any, ack_timeout: float):
        self.conn = conn
        self.ack_timeout = ack_timeout
        self.pending: dict[str, asyncio.Future] = {}
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.minute = None  # type: TokenBucket | None
        self.hour = None  # type: TokenBucket | None
        self.displaced = False

    async def request(self, frame: dict[str, Any]) -> dict[str, Any]:
        req_id = frame["headers"]["req_id"]
        async with self.locks[req_id]:
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self.pending[req_id] = future
            try:
                await self.conn.send(json.dumps(frame, ensure_ascii=False))
                ack = await asyncio.wait_for(future, self.ack_timeout)
            except (TimeoutError, ConnectionError, OSError) as exc:
                raise PlatformError(f"wecom {frame['cmd']}: {type(exc).__name__}") from None
            finally:
                self.pending.pop(req_id, None)
        return ack

    def fail_pending(self) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("wecom connection closed"))
                # Whoever waits on it re-raises; nobody waiting is not an error.
                future.exception()


class WeComPlatform:
    name = "wecom"

    def __init__(
        self,
        ws_url: str = "wss://openws.work.weixin.qq.com",
        *,
        connect: Connect | None = None,
        clock: Clock = time.time,
        sleep: Sleep = asyncio.sleep,
        heartbeat_seconds: float = 30.0,
        ack_timeout: float = 10.0,
        backoff: tuple[float, ...] = _BACKOFF,
    ):
        self._url = ws_url
        self._connect = connect or _connect
        self._clock = clock
        self._sleep = sleep
        self._heartbeat = heartbeat_seconds
        self._ack_timeout = ack_timeout
        self._backoff = backoff
        self._live: dict[str, _Session] = {}

    # -- ChatPlatform (HTTP side is unused) --------------------------------

    def pacing(self, target: Target) -> Pacing:
        return Pacing(
            min_edit_seconds=2.0,
            max_text=_TEXT_BYTES,
            stream_seconds=_STREAM_SECONDS,
            bytes_limit=True,
        )

    def verify(self, headers: dict[str, str], raw_body: bytes) -> None:
        raise NotVerified("wecom has no inbound webhook")

    def handshake(self, headers: dict[str, str], raw_body: bytes) -> dict | None:
        return None

    def parse(self, headers: dict[str, str], raw_body: bytes) -> list[Inbound]:
        return []

    async def send(self, target: Target, message: Message) -> Sent:
        session = self._session(target)
        reply_id = target.handle.get("req_id")
        if not reply_id:
            # Nothing to reply to (e.g. the question is old): push instead.
            await self._push(session, target, message.text, message.open_url)
            return Sent(None, {"mode": "pushed", "final_sent": True})
        stream_id = secrets.token_hex(8)
        ref = {
            "mode": "stream",
            "stream_id": stream_id,
            "req_id": reply_id,
            "started_at": self._clock(),
        }
        await self._stream_frame(
            session, target, ref, message.text, message.open_url, message.final
        )
        if message.stop_reply_id and not message.final:
            await self._push_card(session, target, message.stop_reply_id)
        return Sent(stream_id, ref)

    async def edit(self, target: Target, sent: Sent, message: Message) -> Sent:
        session = self._session(target)
        ref = dict(sent.ref)
        if ref.get("mode") == "pushed":
            if message.final and not ref.get("final_sent"):
                await self._push(session, target, message.text, message.open_url)
                ref["final_sent"] = True
            return Sent(sent.message_id, ref)
        expired = self._clock() - float(ref.get("started_at", 0)) >= _STREAM_SECONDS
        if expired:
            await self._stream_frame(session, target, ref, "Still working…", None, True)
            ref = {**ref, "mode": "pushed", "final_sent": False}
            if message.final:
                await self._push(session, target, message.text, message.open_url)
                ref["final_sent"] = True
            return Sent(sent.message_id, ref)
        await self._stream_frame(
            session, target, ref, message.text, message.open_url, message.final
        )
        return sent

    async def notify(self, target: Target, text: str) -> None:
        # The platform cannot show a line to one person only: plain message.
        await self._push(self._session(target), target, text, None)

    async def leave(self, target: Target) -> None:
        return None  # a bot cannot be removed from a group by the bot itself

    # -- LongConnectionPlatform --------------------------------------------

    async def hold(
        self,
        account_id: str,
        credential: str,
        deliver: Callable[[Inbound], Awaitable[None]],
        heartbeat: Callable[[], Awaitable[bool]],
        stop: asyncio.Event,
    ) -> str:
        step = 0
        while not stop.is_set():
            try:
                conn = await self._connect(self._url)
            except Exception:
                step = await self._pause(step, stop)
                continue
            outcome = await self._serve(conn, account_id, credential, deliver, heartbeat, stop)
            if outcome != "reconnect":
                return outcome
            step = await self._pause(step, stop)
        return "stopped"

    async def _pause(self, step: int, stop: asyncio.Event) -> int:
        delay = self._backoff[min(step, len(self._backoff) - 1)]
        sleeper = asyncio.ensure_future(self._sleep(delay))
        stopper = asyncio.ensure_future(stop.wait())
        await asyncio.wait({sleeper, stopper}, return_when=asyncio.FIRST_COMPLETED)
        for task in (sleeper, stopper):
            task.cancel()
        return step + 1

    async def _serve(
        self,
        conn: Any,
        bot_id: str,
        secret: str,
        deliver: Callable[[Inbound], Awaitable[None]],
        heartbeat: Callable[[], Awaitable[bool]],
        stop: asyncio.Event,
    ) -> str:
        session = _Session(conn, self._ack_timeout)
        session.minute = TokenBucket(30, 60.0, self._clock)
        session.hour = TokenBucket(1000, 3600.0, self._clock)
        tasks: set[asyncio.Task] = set()
        reader = asyncio.ensure_future(self._read(session, bot_id, deliver, tasks))
        try:
            try:
                ack = await session.request(
                    {
                        "cmd": "aibot_subscribe",
                        "headers": {"req_id": _req_id()},
                        "body": {"bot_id": bot_id, "secret": secret},
                    }
                )
            except PlatformError:
                return "reconnect"
            if ack.get("errcode", 0) != 0:
                return "failed"
            self._live[bot_id] = session
            return await self._beat(session, heartbeat, stop, reader)
        finally:
            if self._live.get(bot_id) is session:
                del self._live[bot_id]
            reader.cancel()
            for task in tasks:
                task.cancel()
            session.fail_pending()
            with contextlib.suppress(Exception):
                await conn.close()

    async def _beat(
        self,
        session: _Session,
        heartbeat: Callable[[], Awaitable[bool]],
        stop: asyncio.Event,
        reader: asyncio.Future,
    ) -> str:
        missed = 0
        while True:
            sleeper = asyncio.ensure_future(self._sleep(self._heartbeat))
            stopper = asyncio.ensure_future(stop.wait())
            await asyncio.wait({sleeper, stopper, reader}, return_when=asyncio.FIRST_COMPLETED)
            for task in (sleeper, stopper):
                task.cancel()
            if session.displaced:
                return "displaced"
            if stop.is_set():
                return "stopped"
            if reader.done():
                return "reconnect"
            if not await heartbeat():
                return "stopped"
            try:
                await session.request({"cmd": "ping", "headers": {"req_id": _req_id()}})
                missed = 0
            except PlatformError:
                missed += 1
                if missed >= 2:
                    return "reconnect"

    async def _read(
        self,
        session: _Session,
        bot_id: str,
        deliver: Callable[[Inbound], Awaitable[None]],
        tasks: set[asyncio.Task],
    ) -> None:
        try:
            while True:
                raw = await session.conn.recv()
                try:
                    frame = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(frame, dict):
                    continue
                cmd = frame.get("cmd")
                if cmd is None:
                    future = session.pending.get((frame.get("headers") or {}).get("req_id"))
                    if future is not None and not future.done():
                        future.set_result(frame)
                    continue
                if _is_disconnected(frame):
                    session.displaced = True
                    return
                inbound = self._inbound(frame, bot_id)
                if inbound is not None:
                    task = asyncio.ensure_future(self._deliver(deliver, inbound))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
        except Exception:
            return
        finally:
            session.fail_pending()

    @staticmethod
    async def _deliver(deliver: Callable[[Inbound], Awaitable[None]], inbound: Inbound) -> None:
        try:
            await deliver(inbound)
        except Exception as exc:
            log.warning("wecom delivery failed: %s", type(exc).__name__)

    # -- frames to Inbound ---------------------------------------------------

    def _inbound(self, frame: dict[str, Any], bot_id: str) -> Inbound | None:
        body = frame.get("body")
        req_id = (frame.get("headers") or {}).get("req_id")
        if not isinstance(body, dict) or not req_id:
            return None
        sender = (body.get("from") or {}).get("userid")
        if frame.get("cmd") == "aibot_msg_callback":
            return self._message(body, bot_id, req_id, sender)
        if frame.get("cmd") == "aibot_event_callback":
            return self._event(body, bot_id, req_id, sender)
        return None

    def _message(
        self, body: dict[str, Any], bot_id: str, req_id: str, sender: str | None
    ) -> Inbound | None:
        msgid = body.get("msgid")
        if not msgid or not sender:
            return None
        group = body.get("chattype") == "group" and bool(body.get("chatid"))
        kind = body.get("msgtype")
        texts: list[str] = []
        media = kind in _MEDIA
        if kind == "text":
            texts.append((body.get("text") or {}).get("content") or "")
        elif kind == "mixed":
            for item in (body.get("mixed") or {}).get("msg_item") or []:
                if item.get("msgtype") == "text":
                    texts.append((item.get("text") or {}).get("content") or "")
                else:
                    media = True
        elif not media:
            return None
        text = " ".join(texts)
        code = None
        if group:
            text = _LEADING_MENTION.sub("", text, count=1)
            found = _CODE.search(text)
            code = found.group(0) if found else None
        return Inbound(
            platform=self.name,
            message_key=str(msgid),
            via="message",
            kind="question",
            external_chat_id=str(body["chatid"]) if group else sender,
            chat_kind="group" if group else "dm",
            account_id=bot_id,
            asker_external_id=sender,
            external_thread_key=None if group else "dm",
            text=text.strip(),
            code=code,
            has_attachments=media,
            message_id=str(msgid),
            handle={"req_id": req_id},
        )

    def _event(
        self, body: dict[str, Any], bot_id: str, req_id: str, sender: str | None
    ) -> Inbound | None:
        event = body.get("event") or {}
        name = event.get("eventtype")
        if name != "template_card_event" or not sender:
            return None
        key = str((event.get("template_card_event") or {}).get("event_key") or "")
        if not key.startswith("stop:"):
            return None
        group = body.get("chattype") == "group" and bool(body.get("chatid"))
        return Inbound(
            platform=self.name,
            message_key=f"{sender}:{body.get('create_time')}:{name}",
            via="action",
            kind="stop",
            external_chat_id=str(body["chatid"]) if group else sender,
            chat_kind="group" if group else "dm",
            account_id=bot_id,
            asker_external_id=sender,
            action_value=key[len("stop:") :],
            handle={"req_id": req_id},
        )

    # -- sending -----------------------------------------------------------

    def _session(self, target: Target) -> _Session:
        session = self._live.get(target.account_id or "")
        if session is None:
            raise NotHere("this process does not hold the wecom bot connection")
        return session

    @staticmethod
    def _take(session: _Session) -> None:
        for bucket in (session.minute, session.hour):
            wait = bucket.take() if bucket else 0.0
            if wait > 0:
                raise RateLimited(wait)

    async def _frame(self, session: _Session, frame: dict[str, Any]) -> None:
        self._take(session)
        ack = await session.request(frame)
        code = ack.get("errcode", 0)
        if code != 0:
            raise PlatformError(f"wecom {frame['cmd']} refused ({code})")

    async def _stream_frame(
        self,
        session: _Session,
        target: Target,
        ref: dict[str, Any],
        text: str,
        open_url: str | None,
        finish: bool,
    ) -> None:
        footer = f"\n\n[Open in PuppyOne]({open_url})" if open_url else ""
        content = fit(text or "…", _TEXT_BYTES, bytes_limit=True, footer=footer)
        await self._frame(
            session,
            {
                "cmd": "aibot_respond_msg",
                "headers": {"req_id": ref["req_id"]},
                "body": {
                    "msgtype": "stream",
                    "stream": {"id": ref["stream_id"], "finish": finish, "content": content},
                },
            },
        )

    async def _push(
        self, session: _Session, target: Target, text: str, open_url: str | None
    ) -> None:
        footer = f"\n\n[Open in PuppyOne]({open_url})" if open_url else ""
        await self._frame(
            session,
            {
                "cmd": "aibot_send_msg",
                "headers": {"req_id": _req_id()},
                "body": {
                    "chatid": target.external_chat_id,
                    "chat_type": 1 if target.chat_kind == "dm" else 2,
                    "msgtype": "markdown",
                    "markdown": {
                        "content": fit(text or "…", _TEXT_BYTES, bytes_limit=True, footer=footer)
                    },
                },
            },
        )

    async def _push_card(self, session: _Session, target: Target, reply_id: str) -> None:
        await self._frame(
            session,
            {
                "cmd": "aibot_send_msg",
                "headers": {"req_id": _req_id()},
                "body": {
                    "chatid": target.external_chat_id,
                    "chat_type": 1 if target.chat_kind == "dm" else 2,
                    "msgtype": "template_card",
                    "template_card": {
                        "card_type": "button_interaction",
                        "main_title": {"title": "Working…"},
                        "button_list": [{"text": "Stop", "style": 1, "key": f"stop:{reply_id}"}],
                        "task_id": f"stop-{reply_id}"[:128],
                    },
                },
            },
        )


def _req_id() -> str:
    return secrets.token_hex(8)


def _is_disconnected(frame: dict[str, Any]) -> bool:
    if frame.get("cmd") != "aibot_event_callback":
        return False
    event = (frame.get("body") or {}).get("event") or {}
    return event.get("eventtype") == "disconnected_event"
