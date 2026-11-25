"""A chat platform that records what we send and edit; shared by chat app tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.platform.chat_apps.ports import (
    Inbound,
    Message,
    MessageGone,
    NotVerified,
    Pacing,
    RateLimited,
    Sent,
    Target,
)


@dataclass
class FakePlatform:
    name: str = "telegram"
    secret: str = "fake-webhook-placeholder"
    min_edit_seconds: float = 0.0
    max_text: int = 4096
    sent: list[tuple[Target, Message]] = field(default_factory=list)
    edits: list[tuple[str, Message]] = field(default_factory=list)
    notices: list[tuple[Target, str]] = field(default_factory=list)
    left: list[Target] = field(default_factory=list)
    fail_next: list[Exception] = field(default_factory=list)
    gone: set[str] = field(default_factory=set)

    def pacing(self, target: Target) -> Pacing:
        return Pacing(self.min_edit_seconds, self.max_text)

    def verify(self, headers, raw_body) -> None:
        if headers.get("x-fake-secret") != self.secret:
            raise NotVerified()

    def handshake(self, headers, raw_body):
        body = json.loads(raw_body or b"{}")
        return {"challenge": body["challenge"]} if "challenge" in body else None

    def parse(self, headers, raw_body) -> list[Inbound]:
        body = json.loads(raw_body)
        return [Inbound(platform=self.name, **event) for event in body.get("events", [])]

    def _maybe_fail(self):
        if self.fail_next:
            raise self.fail_next.pop(0)

    async def send(self, target: Target, message: Message) -> Sent:
        self._maybe_fail()
        self.sent.append((target, message))
        return Sent(f"m{len(self.sent)}", {"n": len(self.sent)})

    async def edit(self, target: Target, sent: Sent, message: Message) -> Sent:
        self._maybe_fail()
        if sent.message_id in self.gone:
            raise MessageGone()
        self.edits.append((sent.message_id, message))
        return sent

    async def notify(self, target: Target, text: str) -> None:
        self.notices.append((target, text))

    async def leave(self, target: Target) -> None:
        self.left.append(target)

    def texts(self) -> list[str]:
        return [m.text for _, m in self.sent] + [m.text for _, m in self.edits]


def rate_limited(seconds: float = 3.0) -> RateLimited:
    return RateLimited(seconds)
