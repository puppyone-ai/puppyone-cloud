"""What every chat platform implements. The gateway knows no platform by name.

A platform turns its signed callbacks (or, for WeCom, its long-connection
frames) into ``Inbound`` events, and sends or edits messages for a ``Target``.
Progress is one message that is edited; how a platform shows it (edit, draft,
stream) is the platform's business, kept in ``Sent.ref`` between calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Via = Literal["message", "guest", "action", "membership"]
Kind = Literal["question", "knock", "stop", "joined", "left", "new_thread", "uninstalled"]
Trust = Literal["member", "external", "guest", "bot"]
ChatKind = Literal["group", "dm"]


@dataclass(frozen=True)
class Inbound:
    """One thing a platform told us. Only events addressed to the agent exist."""

    platform: str
    message_key: str
    via: Via
    kind: Kind
    external_chat_id: str
    chat_kind: ChatKind = "group"
    #: Slack team id / WeCom bot id; resolved to our connection row. None for Telegram.
    account_id: str | None = None
    external_event_id: str | None = None
    chat_title: str | None = None
    external_thread_key: str | None = None
    asker_external_id: str | None = None
    asker_label: str | None = None
    asker_trust: Trust = "member"
    #: The question with the mention or ``/ask@bot`` removed.
    text: str = ""
    #: Knock code from ``/start@bot <code>`` or ``@bot 482731``.
    code: str | None = None
    has_attachments: bool = False
    #: Platform message id of this event (to reply under or to edit later).
    message_id: str | None = None
    #: For a Stop click: the reply id carried by the button.
    action_value: str | None = None
    #: Short-lived, platform-specific reply handles (Telegram guest query id,
    #: callback query id, Slack response_url, WeCom req_id). Never secrets.
    handle: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        """What the inbox keeps besides its columns (text is scrubbed after 7 days)."""
        return {
            "text": self.text,
            "code": self.code,
            "chat_kind": self.chat_kind,
            "chat_title": self.chat_title,
            "trust": self.asker_trust,
            "has_attachments": self.has_attachments,
            "message_id": self.message_id,
            "action_value": self.action_value,
            "account_id": self.account_id,
            **{key: value for key, value in self.handle.items() if value is not None},
        }


@dataclass(frozen=True)
class Target:
    """Where a message goes."""

    platform: str
    external_chat_id: str
    chat_kind: ChatKind = "group"
    connection_id: str | None = None
    #: The platform account (Slack team, WeCom bot id); None for Telegram.
    account_id: str | None = None
    #: Decrypted customer credential (Slack bot token, WeCom secret), never logged.
    credential: str | None = field(default=None, repr=False)
    external_thread_key: str | None = None
    #: The message being answered (reply under it / thread on it).
    reply_to: str | None = None
    #: The person being answered, for mentions and private notices.
    asker_external_id: str | None = None
    asker_label: str | None = None
    via: Via = "message"
    handle: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    """What one chat message should say now."""

    text: str
    #: Show a Stop button bound to this reply id.
    stop_reply_id: str | None = None
    #: "Open in PuppyOne" link, appended by the platform in its own format.
    open_url: str | None = None
    #: A single link button (e.g. "Link account").
    button: tuple[str, str] | None = None
    #: The Run reached its end; streams close, drafts become a message.
    final: bool = False
    #: Mention this person (``Done · {asker}``).
    mention: str | None = None


@dataclass(frozen=True)
class Sent:
    """A platform message reference. ``ref`` is persisted between edits."""

    message_id: str | None
    ref: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Pacing:
    """How often one message may change and how long it may be."""

    min_edit_seconds: float
    max_text: int
    #: Streams that must end in time (Slack 10 min, WeCom 10 min); None if none.
    stream_seconds: float | None = None
    stream_chars: int | None = None
    #: True when the length limit is in UTF-8 bytes (WeCom), not characters.
    bytes_limit: bool = False


class PlatformError(Exception):
    """The platform refused or failed; retry later."""


class RateLimited(PlatformError):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited for {retry_after}s")
        self.retry_after = retry_after


class MessageGone(PlatformError):
    """The message to edit was deleted; send a new one."""


class NotVerified(Exception):
    """The request did not come from the platform."""


class ChatPlatform(Protocol):
    name: str

    def pacing(self, target: Target) -> Pacing: ...

    def verify(self, headers: dict[str, str], raw_body: bytes) -> None:
        """Raise ``NotVerified`` unless the platform signed this request."""

    def handshake(self, headers: dict[str, str], raw_body: bytes) -> dict | None:
        """A body to return at once (Slack url_verification), else None."""

    def parse(self, headers: dict[str, str], raw_body: bytes) -> list[Inbound]:
        """Addressed events only; everything else is dropped here, unlogged."""

    async def send(self, target: Target, message: Message) -> Sent: ...

    async def edit(self, target: Target, sent: Sent, message: Message) -> Sent:
        """Change the message in place; raise ``MessageGone`` if it was deleted."""

    async def notify(self, target: Target, text: str) -> None:
        """A line only the asker sees, or a plain reply where that is impossible."""

    async def leave(self, target: Target) -> None:
        """Leave the chat if the platform allows; otherwise do nothing."""


class NotHere(PlatformError):
    """This process does not hold the connection the message must go through."""


class LinkPlatform(Protocol):
    """Telegram: links that put the bot into a chat with a one-time code."""

    def start_links(self, code: str) -> dict[str, str]: ...


class InstallPlatform(Protocol):
    """Slack: one OAuth install per workspace."""

    def authorize_url(self, state: str) -> str: ...

    async def exchange(self, code: str) -> dict[str, str]:
        """``{"team_id", "team_name", "bot_token", "installer_user_id"}``."""


class LongConnectionPlatform(Protocol):
    """WeCom: we connect out and keep one socket per bot (whoever holds the lease)."""

    async def hold(
        self,
        account_id: str,
        credential: str,
        deliver,  # async (Inbound) -> None
        heartbeat,  # async () -> bool; False means our lease is gone
        stop,  # asyncio.Event
    ) -> str:
        """Run until stopped; return ``"stopped"``, ``"failed"`` or ``"displaced"``."""
