"""One inbox event + what the database says now -> what to do. Pure; no I/O.

Checks run in the order of the design (09-chat-apps.md, "Dispatch: checks in
order"); the first that fails decides.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

Action = Literal[
    "drop",
    "notice",
    "knock",
    "submit",
    "wait",
    "hold",
    "stop",
    "removed_there",
    "revoke",
    "new_thread",
    "joined",
]
REQUEST_NAMESPACE = uuid.UUID("5f0f3f6e-8a0e-4f73-9c62-0c6b7d3a2c11")
STALE_AFTER = timedelta(minutes=10)
WECOM_THREAD_IDLE = timedelta(minutes=30)
DM_THREAD_IDLE = timedelta(days=1)
MAX_WAITING = 5
MAX_PER_HOUR = 60
ONCE_AN_HOUR = frozenset({"paused", "limit", "only_owner", "not_in_project"})


@dataclass(frozen=True)
class Decision:
    action: Action
    outcome: str
    notice: str | None = None
    values: dict[str, Any] = field(default_factory=dict)


def request_id(platform: str, message_key: str) -> uuid.UUID:
    """The same platform message always maps to the same Run request."""
    return uuid.uuid5(REQUEST_NAMESPACE, f"{platform}:{message_key}")


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def clean_label(value: str | None) -> str:
    """Names that go into the prompt's first line cannot add lines or controls."""
    return _CONTROL.sub(" ", value or "").replace("[", "(").replace("]", ")").strip()[:64]


def prompt(platform: str, chat: str, asker: str | None, text: str) -> str:
    name = {"telegram": "Telegram", "slack": "Slack", "wecom": "WeCom"}[platform]
    return f"[{name} · {clean_label(chat)} · {clean_label(asker) or 'someone'}]\n{text}"


def thread_key(event: dict) -> str:
    if event.get("external_thread_key"):
        return event["external_thread_key"]
    if (event.get("payload") or {}).get("chat_kind") == "dm":
        return "dm"
    # WeCom groups have no threads: one person's questions within 30 minutes.
    return f"asker:{event.get('asker_external_id') or 'unknown'}"


def _idle(thread: dict | None, platform: str, chat_kind: str, now: datetime) -> bool:
    if not thread or not thread.get("agent_session_id"):
        return False
    limit = (
        DM_THREAD_IDLE if chat_kind == "dm" else WECOM_THREAD_IDLE if platform == "wecom" else None
    )
    if limit is None:
        return False
    last = datetime.fromisoformat(thread["last_activity_at"])
    return now - last > limit


def _once(notice: str, situation: dict) -> Decision:
    if notice in ONCE_AN_HOUR and notice in (situation.get("notified_last_hour") or []):
        return Decision("drop", notice)
    return Decision("notice", notice, notice)


def decide(event: dict, situation: dict, now: datetime) -> Decision:
    payload = event.get("payload") or {}
    kind = event["kind"]
    conversation = situation.get("conversation")
    platform = event["platform"]

    if kind == "uninstalled":
        return Decision("revoke", "uninstalled")
    if kind == "left":
        return Decision("removed_there", "removed_there")
    if kind == "knock":
        return Decision("knock", "knock", values={"code": payload.get("code")})
    if kind == "joined":
        if event.get("asker_external_id"):
            return Decision("knock", "knock", values={"owner": event["asker_external_id"]})
        return Decision("joined", "inviter_missing")
    if kind == "stop":
        return Decision("stop", "stop", values={"reply_id": payload.get("action_value")})

    # 1. Is the speaker trusted?
    trust = payload.get("trust", "member")
    if trust == "bot":
        return Decision("drop", "bot")
    if trust in ("external", "guest"):
        return Decision("notice", "outsider", "outsider")
    # 2. Is this chat connected?
    if conversation is None:
        if platform == "wecom" and payload.get("code"):
            return Decision("knock", "knock", values={"code": payload["code"]})
        if platform == "slack" and situation.get("join_without_inviter"):
            return Decision("knock", "knock", values={"owner": event.get("asker_external_id")})
        if (platform == "slack" and payload.get("chat_kind") != "dm") or event["via"] == "guest":
            return Decision("notice", "not_connected", "not_connected")
        return Decision("drop", "not_connected")
    # 3. Allowed yet?
    if conversation["state"] != "active":
        return Decision("drop", "asking")
    if kind == "new_thread":
        return Decision("new_thread", "new_thread", "new_thread", values={"thread_key": "dm"})
    if (
        now - datetime.fromisoformat(event["created_at"]) > STALE_AFTER
        and event["state"] == "pending"
    ):
        return Decision("notice", "stale", "stale")
    # 4. Enabled?
    if conversation.get("stopped_reason") or not conversation.get("enabled", True):
        return _once("paused", situation)
    # 5. Plan and Agent hours: checked by AgentService.submit (see gateway).
    # 6. May this person ask?
    who = conversation["who_can_ask"]
    asker = event.get("asker_external_id")
    if who == "owner":
        if not (
            (asker and asker == conversation.get("owner_external_id"))
            or (
                situation.get("asker_user_id")
                and situation["asker_user_id"] == conversation["owner_user_id"]
            )
        ):
            return _once("only_owner", situation)
    elif who == "project":
        if not situation.get("asker_user_id"):
            return Decision("hold", "link_account", "link_account")
        if not situation.get("asker_can_run"):
            return _once("not_in_project", situation)
    # 7. Today's limit (and the hourly guard).
    limit = conversation.get("daily_limit_units")
    if limit is not None and situation.get("used_today_units", 0) >= limit:
        return _once("limit", situation)
    if situation.get("asked_last_hour", 0) > MAX_PER_HOUR:
        return Decision("notice", "too_many", "too_many")
    # 8. Text only.
    text = (payload.get("text") or "").strip()
    if not text:
        if payload.get("has_attachments"):
            return Decision("notice", "attachments", "attachments")
        return Decision("drop", "empty")
    # 9. Which thread.
    thread = situation.get("thread")
    key = (thread or {}).get("external_thread_key") or thread_key(event)
    fresh = _idle(thread, platform, conversation["chat_kind"], now)
    # 10. Busy?
    if thread and thread.get("busy"):
        if thread.get("waiting", 0) >= MAX_WAITING:
            return Decision("notice", "too_many", "too_many")
        return Decision("wait", "queued", "queued", values={"thread_key": key})
    # 11. Submit as the owner.
    return Decision(
        "submit",
        "submitted",
        values={
            "thread_key": key,
            "fresh_session": fresh,
            "request_id": str(request_id(platform, event["message_key"])),
            "prompt": prompt(
                platform, conversation["display_name"], event.get("asker_label"), text
            ),
        },
    )
