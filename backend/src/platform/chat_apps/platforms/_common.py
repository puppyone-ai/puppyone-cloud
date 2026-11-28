"""Small helpers shared by the platform adapters. No platform knowledge here."""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping

Clock = Callable[[], float]


def header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup; empty string when absent."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def safe_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class TokenBucket:
    """``capacity`` takes per ``per_seconds``, refilled continuously."""

    def __init__(self, capacity: int, per_seconds: float, clock: Clock):
        self._capacity = float(capacity)
        self._rate = capacity / per_seconds
        self._clock = clock
        self._tokens = float(capacity)
        self._at = clock()

    def take(self) -> float:
        """0.0 when a token was taken, else the seconds until one is free."""
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + max(0.0, now - self._at) * self._rate)
        self._at = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        return (1.0 - self._tokens) / self._rate


def _size(text: str, bytes_limit: bool) -> int:
    return len(text.encode("utf-8")) if bytes_limit else len(text)


def _cut(text: str, limit: int, bytes_limit: bool) -> str:
    if bytes_limit:
        return text.encode("utf-8")[: max(limit, 0)].decode("utf-8", errors="ignore")
    return text[: max(limit, 0)]


def fit(text: str, limit: int, *, bytes_limit: bool = False, footer: str = "") -> str:
    """Make ``text + footer`` fit; a cut body ends with an ellipsis before the footer."""
    room = limit - _size(footer, bytes_limit)
    if _size(text, bytes_limit) <= room:
        return text + footer
    return _cut(text, room - _size("…", bytes_limit), bytes_limit) + "…" + footer


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2
