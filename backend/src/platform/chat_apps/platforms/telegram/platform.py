"""Telegram: webhook updates in, Bot API calls out.

Thread key choice: Telegram does not give the head of a reply chain (a reply's
``reply_to_message`` carries no further ``reply_to_message``), so a group
question's thread key is the id of the message it replies to, or its own id
when it starts a chain. DMs are one conversation: the fixed key ``"dm"``.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from typing import Any

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
from .._common import Clock, fit, header, safe_equal, utf16_len

_COMMAND = re.compile(r"^/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?:\s+(.*))?$", re.DOTALL)
_ATTACHMENTS = (
    "photo",
    "document",
    "video",
    "audio",
    "voice",
    "video_note",
    "animation",
    "sticker",
)
_MAX_TEXT = 4096
_OPEN_LABEL = "Open in PuppyOne"


class _NotModified(PlatformError):
    """Telegram refused an edit because nothing changed."""


class TelegramPlatform:
    name = "telegram"

    def __init__(
        self,
        bot_token: str,
        bot_username: str,
        webhook_secret: str,
        api_base: str = "https://api.telegram.org",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = time.time,
        timeout: float = 15.0,
    ):
        self._token = bot_token
        self._username = bot_username.lstrip("@")
        self._secret = webhook_secret
        self._base = api_base.rstrip("/")
        self._bot_id = bot_token.split(":", 1)[0]
        self._transport = transport
        self._clock = clock
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # -- ChatPlatform ------------------------------------------------------

    def pacing(self, target: Target) -> Pacing:
        # Groups: ~20 sends+edits a minute per chat, so one edit per 3 s.
        # DM drafts expire after 30 s: the relay must refresh within 20 s.
        if target.chat_kind == "dm" and target.via != "guest":
            return Pacing(min_edit_seconds=1.0, max_text=_MAX_TEXT)
        return Pacing(min_edit_seconds=3.0, max_text=_MAX_TEXT)

    def verify(self, headers: dict[str, str], raw_body: bytes) -> None:
        got = header(headers, "X-Telegram-Bot-Api-Secret-Token")
        if not self._secret or not safe_equal(got, self._secret):
            raise NotVerified("telegram secret token mismatch")

    def handshake(self, headers: dict[str, str], raw_body: bytes) -> dict | None:
        return None

    def parse(self, headers: dict[str, str], raw_body: bytes) -> list[Inbound]:
        try:
            update = json.loads(raw_body)
        except ValueError:
            return []
        if not isinstance(update, dict):
            return []
        update_id = update.get("update_id")
        for key, via in (("message", "message"), ("guest_message", "guest")):
            if isinstance(update.get(key), dict):
                event = self._from_message(update[key], via, update_id)
                return [event] if event else []
        if isinstance(update.get("callback_query"), dict):
            event = self._from_callback(update["callback_query"], update_id)
            return [event] if event else []
        if isinstance(update.get("my_chat_member"), dict):
            event = self._from_membership(update["my_chat_member"], update_id)
            return [event] if event else []
        return []

    async def send(self, target: Target, message: Message) -> Sent:
        text = self._text(message)
        entities = self._entities(message, text, target)
        markup = self._markup(message)
        if self._is_guest(target):
            query_id = target.handle.get("guest_query_id")
            if not query_id:
                raise PlatformError("telegram guest target without a guest query id")
            result = await self._call(
                "answerGuestQuery",
                {
                    "guest_query_id": query_id,
                    "result": _drop_none(
                        {
                            "type": "article",
                            "id": str(query_id)[:64],
                            "title": "PuppyOne",
                            "input_message_content": {"message_text": text},
                            "reply_markup": markup,
                        }
                    ),
                },
            )
            inline_id = (result or {}).get("inline_message_id")
            return Sent(inline_id, {"mode": "guest", "inline_message_id": inline_id})
        if target.chat_kind == "dm" and not message.final:
            draft_id = max(int(self._clock() * 1000) % 2_147_483_647, 1)
            await self._draft(target, draft_id, text)
            return Sent(None, {"mode": "draft", "draft_id": draft_id})
        return await self._send_message(target, text, entities, markup)

    async def edit(self, target: Target, sent: Sent, message: Message) -> Sent:
        text = self._text(message)
        entities = self._entities(message, text, target)
        mode = sent.ref.get("mode")
        if mode == "draft":
            if not message.final:
                await self._draft(target, int(sent.ref["draft_id"]), text)
                return sent
            return await self._send_message(target, text, entities, self._markup(message))
        payload: dict[str, Any] = {
            "text": text,
            "reply_markup": self._markup(message) or {"inline_keyboard": []},
        }
        if entities:
            payload["entities"] = entities
        if mode == "guest":
            payload["inline_message_id"] = sent.ref["inline_message_id"]
        else:
            payload["chat_id"] = target.external_chat_id
            payload["message_id"] = int(sent.message_id or 0)
        with contextlib.suppress(_NotModified):
            await self._call("editMessageText", payload)
        return sent

    async def notify(self, target: Target, text: str) -> None:
        callback_id = target.handle.get("callback_query_id")
        if callback_id:
            await self._call(
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": fit(text, 200), "show_alert": False},
            )
            return
        if self._is_guest(target):
            return  # a guest bot may answer once; there is no second message
        await self._send_message(target, fit(text, _MAX_TEXT), [], None)

    async def leave(self, target: Target) -> None:
        if target.chat_kind == "group":
            await self._call("leaveChat", {"chat_id": target.external_chat_id})

    # -- LinkPlatform ------------------------------------------------------

    def start_links(self, code: str) -> dict[str, str]:
        base = f"https://t.me/{self._username}"
        return {"group": f"{base}?startgroup={code}", "dm": f"{base}?start={code}"}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- parsing -----------------------------------------------------------

    def _is_bot_message(self, reply: dict[str, Any] | None) -> bool:
        sender = (reply or {}).get("from") or {}
        if not sender:
            return False
        if str(sender.get("id", "")) == self._bot_id:
            return True
        return str(sender.get("username", "")).lower() == self._username.lower()

    def _strip_mention(self, text: str) -> tuple[str, bool]:
        pattern = rf"(?<![A-Za-z0-9_])@{re.escape(self._username)}(?![A-Za-z0-9_])"
        stripped, count = re.subn(pattern, "", text, flags=re.IGNORECASE)
        return stripped.strip(), count > 0

    def _from_message(self, msg: dict[str, Any], via: str, update_id: Any) -> Inbound | None:
        chat = msg.get("chat") or msg.get("guest_bot_caller_chat") or {}
        sender = msg.get("from") or msg.get("guest_bot_caller_user") or {}
        if chat.get("id") is None or msg.get("message_id") is None:
            return None
        if sender.get("is_bot"):
            return None
        chat_type = chat.get("type")
        if chat_type not in {"private", "group", "supergroup"}:
            return None
        guest = via == "guest"
        # A guest query's chat may be somebody else's private chat: never a DM with us.
        is_dm = chat_type == "private" and not guest
        chat_id = str(chat["id"])
        message_id = str(msg["message_id"])
        raw_text = msg.get("text") or msg.get("caption") or ""
        reply = msg.get("reply_to_message")
        base: dict[str, Any] = {
            "platform": self.name,
            "message_key": f"{chat_id}:{message_id}",
            "via": via,
            "external_chat_id": chat_id,
            "chat_kind": "dm" if is_dm else "group",
            "external_event_id": str(update_id) if update_id is not None else None,
            "chat_title": chat.get("title") or chat.get("first_name"),
            "asker_external_id": str(sender["id"]) if sender.get("id") is not None else None,
            "asker_label": sender.get("username") or sender.get("first_name"),
            "has_attachments": any(key in msg for key in _ATTACHMENTS),
            "message_id": message_id,
        }
        if guest and msg.get("guest_query_id"):
            base["handle"] = {"guest_query_id": msg["guest_query_id"]}
        thread = "dm" if is_dm else str((reply or {}).get("message_id") or message_id)

        command = _COMMAND.match(raw_text.strip())
        if command:
            name, addressed_to, rest = (
                command.group(1).lower(),
                command.group(2),
                command.group(3) or "",
            )
            if addressed_to and addressed_to.lower() != self._username.lower():
                return None
            if not addressed_to and not is_dm:
                return None
            if name == "start":
                code = rest.split()[0] if rest.split() else None
                return Inbound(kind="knock", code=code, **base) if code else None
            if name == "ask":
                return Inbound(
                    kind="question", text=rest.strip(), external_thread_key=thread, **base
                )
            if name == "new" and is_dm:
                return Inbound(kind="new_thread", external_thread_key="dm", **base)
            return None

        text, mentioned = self._strip_mention(raw_text)
        if is_dm or guest or mentioned or self._is_bot_message(reply):
            return Inbound(kind="question", text=text, external_thread_key=thread, **base)
        return None

    def _from_callback(self, query: dict[str, Any], update_id: Any) -> Inbound | None:
        data = query.get("data") or ""
        sender = query.get("from") or {}
        if not data.startswith("stop:") or sender.get("is_bot") or query.get("id") is None:
            return None
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id", sender.get("id"))
        if chat_id is None:
            return None
        return Inbound(
            platform=self.name,
            message_key=f"cb:{query['id']}",
            via="action",
            kind="stop",
            external_chat_id=str(chat_id),
            chat_kind="dm" if chat.get("type") == "private" else "group",
            external_event_id=str(update_id) if update_id is not None else None,
            asker_external_id=str(sender["id"]) if sender.get("id") is not None else None,
            asker_label=sender.get("username") or sender.get("first_name"),
            message_id=str(message["message_id"])
            if message.get("message_id") is not None
            else None,
            action_value=data[len("stop:") :],
            handle={
                "callback_query_id": query["id"],
                **(
                    {"inline_message_id": query["inline_message_id"]}
                    if query.get("inline_message_id")
                    else {}
                ),
            },
        )

    def _from_membership(self, change: dict[str, Any], update_id: Any) -> Inbound | None:
        status = (change.get("new_chat_member") or {}).get("status")
        chat = change.get("chat") or {}
        if status not in {"left", "kicked"} or chat.get("id") is None:
            return None
        who = change.get("from") or {}
        return Inbound(
            platform=self.name,
            message_key=f"mcm:{chat['id']}:{update_id}",
            via="membership",
            kind="left",
            external_chat_id=str(chat["id"]),
            chat_kind="dm" if chat.get("type") == "private" else "group",
            external_event_id=str(update_id) if update_id is not None else None,
            chat_title=chat.get("title") or chat.get("first_name"),
            asker_external_id=str(who["id"]) if who.get("id") is not None else None,
        )

    # -- sending -----------------------------------------------------------

    @staticmethod
    def _is_guest(target: Target) -> bool:
        return target.via == "guest" or bool(target.handle.get("guest_query_id"))

    @staticmethod
    def _text(message: Message) -> str:
        return fit(message.text or "…", _MAX_TEXT)

    @staticmethod
    def _markup(message: Message) -> dict[str, Any] | None:
        rows: list[list[dict[str, str]]] = []
        if message.stop_reply_id and not message.final:
            rows.append([{"text": "Stop", "callback_data": f"stop:{message.stop_reply_id}"}])
        if message.button:
            rows.append([{"text": message.button[0], "url": message.button[1]}])
        if message.open_url:
            rows.append([{"text": _OPEN_LABEL, "url": message.open_url}])
        return {"inline_keyboard": rows} if rows else None

    @staticmethod
    def _entities(message: Message, text: str, target: Target) -> list[dict[str, Any]]:
        """Turn the asker's name inside the text into a real mention."""
        who = target.asker_external_id
        if not message.mention or not who or not who.isdigit():
            return []
        at = text.find(message.mention)
        if at < 0:
            return []
        return [
            {
                "type": "text_mention",
                "offset": utf16_len(text[:at]),
                "length": utf16_len(message.mention),
                "user": {"id": int(who)},
            }
        ]

    async def _send_message(
        self,
        target: Target,
        text: str,
        entities: list[dict[str, Any]],
        markup: dict[str, Any] | None,
    ) -> Sent:
        payload: dict[str, Any] = {"chat_id": target.external_chat_id, "text": text}
        if entities:
            payload["entities"] = entities
        if markup:
            payload["reply_markup"] = markup
        if target.reply_to and target.chat_kind == "group":
            payload["reply_parameters"] = {
                "message_id": int(target.reply_to),
                "allow_sending_without_reply": True,
            }
        result = await self._call("sendMessage", payload)
        message_id = str((result or {}).get("message_id"))
        return Sent(message_id, {"mode": "chat"})

    async def _draft(self, target: Target, draft_id: int, text: str) -> None:
        await self._call(
            "sendMessageDraft",
            {"chat_id": target.external_chat_id, "draft_id": draft_id, "text": text},
        )

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
        try:
            response = await self._client.post(
                f"{self._base}/bot{self._token}/{method}", json=payload
            )
        except httpx.HTTPError as exc:
            raise PlatformError(f"telegram {method}: {type(exc).__name__}") from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.status_code == 200 and data.get("ok"):
            return data.get("result")
        code = data.get("error_code", response.status_code)
        description = str(data.get("description", "")).lower()
        if code == 429:
            retry = (
                (data.get("parameters") or {}).get("retry_after")
                or response.headers.get("retry-after")
                or 1
            )
            raise RateLimited(float(retry))
        if "message is not modified" in description:
            raise _NotModified("not modified")
        if any(
            marker in description
            for marker in (
                "message to edit not found",
                "message_id_invalid",
                "message can't be edited",
            )
        ):
            raise MessageGone("telegram message is gone")
        raise PlatformError(f"telegram {method} failed ({code})")


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}
