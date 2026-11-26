"""Slack: Events API and interactivity in, Web API calls out.

Progress is a stream (``chat.startStream`` / ``appendStream`` / ``stopStream``)
in the question's thread. A stream message cannot carry buttons, so the Stop
button lives in a small companion message that is deleted when the stream ends.
``Sent.ref`` keeps what was already streamed so only the new suffix is appended.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

from ...ports import (
    Inbound,
    Message,
    MessageGone,
    NotVerified,
    Pacing,
    PlatformError,
    RateLimited,
    Sent,
    Target,
)
from .._common import Clock, TokenBucket, header, safe_equal

SCOPES = "app_mentions:read,chat:write,im:history,channels:read,groups:read"
_STREAM_SECONDS = 9 * 60
_STREAM_CHARS = 11_000
_MAX_TEXT = 12_000
_MAX_SKEW = 5 * 60
_IGNORED_SUBTYPES = {"bot_message", "message_changed", "message_deleted"}


class SlackPlatform:
    name = "slack"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        signing_secret: str,
        public_api_url: str,
        api_base: str = "https://slack.com/api",
        authorize_base: str = "https://slack.com/oauth/v2/authorize",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = time.time,
        timeout: float = 15.0,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._signing = signing_secret
        self._redirect = f"{public_api_url.rstrip('/')}/api/v1/chat-apps/slack/oauth/callback"
        self._base = api_base.rstrip("/")
        self._authorize = authorize_base
        self._transport = transport
        self._clock = clock
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._buckets: dict[tuple[str, str], TokenBucket] = {}

    # -- ChatPlatform ------------------------------------------------------

    def pacing(self, target: Target) -> Pacing:
        return Pacing(
            min_edit_seconds=1.0,
            max_text=_MAX_TEXT,
            stream_seconds=_STREAM_SECONDS,
            stream_chars=_STREAM_CHARS,
        )

    def verify(self, headers: dict[str, str], raw_body: bytes) -> None:
        stamp = header(headers, "X-Slack-Request-Timestamp")
        signature = header(headers, "X-Slack-Signature")
        try:
            sent_at = int(stamp)
        except ValueError:
            raise NotVerified("slack timestamp missing") from None
        if abs(self._clock() - sent_at) > _MAX_SKEW:
            raise NotVerified("slack timestamp is stale")
        base = b"v0:" + stamp.encode() + b":" + raw_body
        expected = "v0=" + hmac.new(self._signing.encode(), base, hashlib.sha256).hexdigest()
        if not self._signing or not safe_equal(signature, expected):
            raise NotVerified("slack signature mismatch")

    def handshake(self, headers: dict[str, str], raw_body: bytes) -> dict | None:
        payload = _json(raw_body)
        if payload.get("type") == "url_verification" and "challenge" in payload:
            return {"challenge": payload["challenge"]}
        return None

    def parse(self, headers: dict[str, str], raw_body: bytes) -> list[Inbound]:
        if raw_body.startswith(b"payload="):
            return self._from_interactivity(raw_body)
        payload = _json(raw_body)
        if payload.get("type") != "event_callback":
            return []
        event = payload.get("event")
        if not isinstance(event, dict):
            return []
        kind = event.get("type")
        if kind in {"app_uninstalled", "tokens_revoked"}:
            return self._uninstalled(payload)
        if event.get("bot_id"):
            return []
        if kind == "app_mention":
            found = self._mention(payload, event)
        elif kind == "message":
            found = self._direct_message(payload, event)
        elif kind == "member_joined_channel":
            found = self._joined(payload, event)
        else:
            found = None
        return [found] if found else []

    async def send(self, target: Target, message: Message) -> Sent:
        # Status lines are a plain message with Stop, updated in place; only
        # the answer itself streams.
        if message.stop_reply_id and not message.final and not message.status:
            return await self._start_stream(target, message)
        text = self._mentioned(message, target)
        body = self._post_body(target, text, message)
        data = await self._call("chat.postMessage", target, body)
        return Sent(data.get("ts"), {"mode": "message"})

    async def edit(self, target: Target, sent: Sent, message: Message) -> Sent:
        answering = message.stop_reply_id and not message.final and not message.status
        if sent.ref.get("mode") != "stream" and answering:
            # The answer begins: the status message keeps only Stop, the answer streams.
            self._take(target, "update", 50)
            await self._call(
                "chat.update",
                target,
                {
                    **self._post_body(
                        target, "", Message("", stop_reply_id=message.stop_reply_id), update=True
                    ),
                    "ts": sent.message_id,
                },
            )
            ref = await self._open_stream(target, message.text, {"mode": "stream", "offset": 0})
            ref["stop_ts"] = sent.message_id
            return Sent(ref["ts"], ref)
        if sent.ref.get("mode") != "stream":
            self._take(target, "update", 50)
            body = self._post_body(target, self._mentioned(message, target), message, update=True)
            body["ts"] = sent.message_id
            await self._call("chat.update", target, body)
            return sent
        return await self._edit_stream(target, sent, message)

    async def notify(self, target: Target, text: str) -> None:
        body: dict[str, Any] = {"channel": target.external_chat_id, "text": text}
        thread = self._thread_ts(target)
        if thread:
            body["thread_ts"] = thread
        if target.asker_external_id:
            body["user"] = target.asker_external_id
            await self._call("chat.postEphemeral", target, body)
        else:
            await self._call("chat.postMessage", target, body)

    async def leave(self, target: Target) -> None:
        return None  # leaving needs a scope we do not request

    # -- InstallPlatform ---------------------------------------------------

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "scope": SCOPES,
                "redirect_uri": self._redirect,
                "state": state,
            }
        )
        return f"{self._authorize}?{query}"

    async def exchange(self, code: str) -> dict[str, str]:
        client = self._http()
        try:
            response = await client.post(
                f"{self._base}/oauth.v2.access",
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect,
                },
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise PlatformError("slack oauth.v2.access failed") from None
        if not isinstance(data, dict) or not data.get("ok"):
            error = data.get("error", "unknown") if isinstance(data, dict) else "unknown"
            raise PlatformError(f"slack oauth failed: {error}")
        team = data.get("team") or {}
        return {
            "team_id": str(team.get("id", "")),
            "team_name": str(team.get("name", "")),
            "bot_token": str(data.get("access_token", "")),
            "installer_user_id": str((data.get("authed_user") or {}).get("id", "")),
            "bot_user_id": str(data.get("bot_user_id", "")),
        }

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- parsing -----------------------------------------------------------

    @staticmethod
    def _bot_user(payload: dict[str, Any]) -> str | None:
        for auth in payload.get("authorizations") or []:
            if auth.get("is_bot") and auth.get("user_id"):
                return str(auth["user_id"])
        return None

    @staticmethod
    def _trust(payload: dict[str, Any], event: dict[str, Any]) -> str:
        profile = event.get("user_profile") or {}
        if (
            event.get("is_restricted")
            or event.get("is_ultra_restricted")
            or profile.get("is_restricted")
            or profile.get("is_ultra_restricted")
        ):
            return "guest"
        team = payload.get("team_id")
        theirs = event.get("user_team") or event.get("source_team")
        if team and theirs and theirs != team:
            return "external"
        return "member"

    def _fields(self, payload: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        return {
            "platform": self.name,
            "via": "message",
            "account_id": payload.get("team_id"),
            "external_event_id": payload.get("event_id"),
            "asker_external_id": event.get("user"),
            "asker_trust": self._trust(payload, event),
            "has_attachments": bool(event.get("files")),
            "message_id": event.get("ts"),
        }

    def _mention(self, payload: dict[str, Any], event: dict[str, Any]) -> Inbound | None:
        if event.get("subtype") in _IGNORED_SUBTYPES:
            return None
        team, channel, ts = payload.get("team_id"), event.get("channel"), event.get("ts")
        if not (team and channel and ts and event.get("user")):
            return None
        bot = self._bot_user(payload)
        text = event.get("text") or ""
        pattern = (
            rf"<@{re.escape(bot)}(?:\|[^>]*)?>" if bot else r"^\s*<@[UW][A-Z0-9]+(?:\|[^>]*)?>"
        )
        text = re.sub(pattern, "", text, count=0 if bot else 1).strip()
        return Inbound(
            message_key=f"{team}:{channel}:{ts}",
            kind="question",
            external_chat_id=channel,
            chat_kind="dm" if event.get("channel_type") == "im" else "group",
            external_thread_key=event.get("thread_ts") or ts,
            text=text,
            **self._fields(payload, event),
        )

    def _direct_message(self, payload: dict[str, Any], event: dict[str, Any]) -> Inbound | None:
        if event.get("channel_type") != "im" or event.get("subtype") not in (None, "file_share"):
            return None
        team, channel, ts, user = (
            payload.get("team_id"),
            event.get("channel"),
            event.get("ts"),
            event.get("user"),
        )
        if not (team and channel and ts and user) or user == self._bot_user(payload):
            return None
        return Inbound(
            message_key=f"{team}:{channel}:{ts}",
            kind="question",
            external_chat_id=channel,
            chat_kind="dm",
            external_thread_key="dm",
            text=(event.get("text") or "").strip(),
            **self._fields(payload, event),
        )

    def _joined(self, payload: dict[str, Any], event: dict[str, Any]) -> Inbound | None:
        bot = self._bot_user(payload)
        team, channel = payload.get("team_id"), event.get("channel")
        if not (bot and team and channel and event.get("user") == bot and payload.get("event_id")):
            return None
        return Inbound(
            platform=self.name,
            message_key=f"{team}:{payload['event_id']}",
            via="membership",
            kind="joined",
            external_chat_id=channel,
            account_id=team,
            external_event_id=payload["event_id"],
            asker_external_id=event.get("inviter"),
        )

    def _uninstalled(self, payload: dict[str, Any]) -> list[Inbound]:
        team, event_id = payload.get("team_id"), payload.get("event_id")
        if not (team and event_id):
            return []
        return [
            Inbound(
                platform=self.name,
                message_key=f"uninstall:{event_id}",
                via="membership",
                kind="uninstalled",
                external_chat_id=team,
                account_id=team,
                external_event_id=event_id,
            )
        ]

    def _from_interactivity(self, raw_body: bytes) -> list[Inbound]:
        try:
            values = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
            payload = json.loads(values["payload"][0])
        except (KeyError, IndexError, ValueError):
            return []
        if not isinstance(payload, dict) or payload.get("type") != "block_actions":
            return []
        action = next(
            (a for a in payload.get("actions") or [] if a.get("action_id") == "stop"), None
        )
        user = payload.get("user") or {}
        channel = (payload.get("channel") or {}).get("id")
        team = (payload.get("team") or {}).get("id")
        trigger = payload.get("trigger_id") or (action or {}).get("action_ts")
        if not (action and channel and team and trigger and user.get("id")):
            return []
        message = payload.get("message") or {}
        return [
            Inbound(
                platform=self.name,
                message_key=f"act:{trigger}",
                via="action",
                kind="stop",
                external_chat_id=channel,
                chat_kind="dm" if channel.startswith("D") else "group",
                account_id=team,
                external_thread_key=message.get("thread_ts") or None,
                asker_external_id=user["id"],
                asker_label=user.get("username") or user.get("name"),
                message_id=message.get("ts"),
                action_value=action.get("value"),
                handle={"response_url": payload.get("response_url")},
            )
        ]

    # -- sending -----------------------------------------------------------

    @staticmethod
    def _thread_ts(target: Target) -> str | None:
        key = target.external_thread_key
        return key if key and key != "dm" else target.reply_to

    @staticmethod
    def _mentioned(message: Message, target: Target) -> str:
        text = message.text
        if message.mention and target.asker_external_id and message.mention in text:
            text = text.replace(message.mention, f"<@{target.asker_external_id}>", 1)
        return text

    def _post_body(
        self, target: Target, text: str, message: Message, *, update: bool = False
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"channel": target.external_chat_id, "text": text or " "}
        thread = self._thread_ts(target)
        if thread and not update:
            body["thread_ts"] = thread
        blocks: list[dict[str, Any]] = []
        if text:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
        buttons: list[dict[str, Any]] = []
        if message.stop_reply_id and not message.final:
            buttons.append(_stop_button(message.stop_reply_id))
        if message.button:
            buttons.append(_link_button(*message.button))
        if buttons:
            blocks.append({"type": "actions", "elements": buttons})
        if message.open_url:
            blocks.append(_open_context(message.open_url))
        if len(blocks) > (1 if text else 0):
            body["blocks"] = blocks
        return body

    async def _start_stream(self, target: Target, message: Message) -> Sent:
        ref = await self._open_stream(target, message.text, {"mode": "stream", "offset": 0})
        stop_ts = None
        if message.stop_reply_id:
            companion = await self._call(
                "chat.postMessage",
                target,
                {
                    "channel": target.external_chat_id,
                    "text": "Stop",
                    "thread_ts": self._thread_ts(target),
                    "blocks": [
                        {"type": "actions", "elements": [_stop_button(message.stop_reply_id)]}
                    ],
                },
            )
            stop_ts = companion.get("ts")
        ref["stop_ts"] = stop_ts
        return Sent(ref["ts"], ref)

    async def _open_stream(self, target: Target, text: str, ref: dict[str, Any]) -> dict[str, Any]:
        self._take(target, "start", 20)
        body: dict[str, Any] = {"channel": target.external_chat_id}
        thread = self._thread_ts(target)
        if thread:
            body["thread_ts"] = thread
        if target.account_id:
            body["recipient_team_id"] = target.account_id
        if target.asker_external_id:
            body["recipient_user_id"] = target.asker_external_id
        if text:
            body["markdown_text"] = text
        data = await self._call("chat.startStream", target, body)
        opened = {**ref, "ts": data.get("ts"), "streamed": len(text), "started_at": self._clock()}
        if not ref.get("offset"):
            opened["sha"] = _sha(text)
        return opened

    async def _edit_stream(self, target: Target, sent: Sent, message: Message) -> Sent:
        ref = dict(sent.ref)
        if message.status and not message.final:
            return sent  # a stream only grows; the status line has nowhere to go
        full = message.text
        channel = target.external_chat_id
        done = int(ref.get("offset", 0)) + int(ref.get("streamed", 0))
        if ref.get("sha") and _sha(full[:done]) != ref["sha"]:
            # The text changed behind what was streamed: close it, say it again in full.
            if ref.get("ts") is not None:
                await self._stop_stream(target, ref, None)
            ref = {**ref, "offset": 0, "streamed": 0, "ts": None}
            done = 0
        pending = full[done:]
        while True:
            if ref.get("ts") is None:
                if not pending:
                    break
                first = pending[:_STREAM_CHARS]
                ref = await self._open_stream(target, first, {**ref, "offset": done})
                pending = pending[len(first) :]
                done += len(first)
                continue
            room = _STREAM_CHARS - int(ref["streamed"])
            expired = self._clock() - float(ref["started_at"]) >= _STREAM_SECONDS
            chunk = pending[: max(room, 0)]
            if chunk:
                await self._call(
                    "chat.appendStream",
                    target,
                    {"channel": channel, "ts": ref["ts"], "markdown_text": chunk},
                )
                ref["streamed"] = int(ref["streamed"]) + len(chunk)
                pending = pending[len(chunk) :]
                done += len(chunk)
            if pending or expired:
                await self._stop_stream(target, ref, None)
                ref = {**ref, "offset": done, "streamed": 0, "ts": None}
                continue
            break
        if message.final:
            if ref.get("ts") is not None:
                await self._stop_stream(target, ref, message.open_url)
            elif message.open_url:
                await self._call(
                    "chat.postMessage",
                    target,
                    {
                        "channel": channel,
                        "text": "Open in PuppyOne",
                        "thread_ts": self._thread_ts(target),
                        "blocks": [_open_context(message.open_url)],
                    },
                )
            await self._drop_companion(target, ref)
            ref = {**ref, "ts": None, "stop_ts": None, "closed": True}
        ref["sha"] = _sha(full[:done])
        return Sent(ref.get("ts") or sent.message_id, ref)

    async def _stop_stream(self, target: Target, ref: dict[str, Any], open_url: str | None) -> None:
        body: dict[str, Any] = {"channel": target.external_chat_id, "ts": ref["ts"]}
        if open_url:
            body["blocks"] = [_open_context(open_url)]
        # MessageGone: Slack's own timeout already closed it; what was streamed is kept.
        with contextlib.suppress(MessageGone):
            await self._call("chat.stopStream", target, body)

    async def _drop_companion(self, target: Target, ref: dict[str, Any]) -> None:
        if not ref.get("stop_ts"):
            return
        # A lingering Stop button is harmless; dispatch answers "already finished".
        with contextlib.suppress(PlatformError):
            await self._call(
                "chat.delete", target, {"channel": target.external_chat_id, "ts": ref["stop_ts"]}
            )

    def _take(self, target: Target, kind: str, per_minute: int) -> None:
        key = (target.account_id or "-", kind)
        bucket = self._buckets.setdefault(key, TokenBucket(per_minute, 60.0, self._clock))
        wait = bucket.take()
        if wait > 0:
            raise RateLimited(wait)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
        return self._client

    async def chat_title(self, target: Target) -> str | None:
        """A channel's name, for a chat that knocks: events carry only its id.

        Read methods take form fields, not JSON. ``None`` when it cannot be read.
        """
        if target.chat_kind != "group":
            return None
        try:
            data = await self._call(
                "conversations.info", target, {"channel": target.external_chat_id}, form=True
            )
        except PlatformError:
            return None
        name = (data.get("channel") or {}).get("name")
        return f"#{name}" if name else None

    async def _call(
        self, method: str, target: Target, body: dict[str, Any], *, form: bool = False
    ) -> dict[str, Any]:
        if not target.credential:
            raise PlatformError("slack call without a bot token")
        body = {key: value for key, value in body.items() if value is not None}
        try:
            response = await self._http().post(
                f"{self._base}/{method}",
                **({"data": body} if form else {"json": body}),
                headers={"Authorization": f"Bearer {target.credential}"},
            )
        except httpx.HTTPError as exc:
            raise PlatformError(f"slack {method}: {type(exc).__name__}") from None
        if response.status_code == 429:
            raise RateLimited(_retry_after(response))
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.status_code == 200 and data.get("ok"):
            return data
        error = str(data.get("error", response.status_code))
        if error == "ratelimited":
            raise RateLimited(_retry_after(response))
        if error in {"message_not_found", "message_not_in_streaming_state", "cant_update_message"}:
            raise MessageGone(error)
        raise PlatformError(f"slack {method} failed: {error}")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("retry-after", "1"))
    except ValueError:
        return 1.0


def _stop_button(reply_id: str) -> dict[str, Any]:
    return {
        "type": "button",
        "action_id": "stop",
        "value": reply_id,
        "text": {"type": "plain_text", "text": "Stop"},
    }


def _link_button(label: str, url: str) -> dict[str, Any]:
    return {"type": "button", "text": {"type": "plain_text", "text": label}, "url": url}


def _open_context(url: str) -> dict[str, Any]:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"<{url}|Open in PuppyOne>"}],
    }
