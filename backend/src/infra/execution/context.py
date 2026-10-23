"""Cooperative execution state; never an authorization or business context."""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

MAX_SECONDS = 300


@dataclass
class Execution:
    deadline: float = field(default_factory=lambda: time.monotonic() + MAX_SECONDS)
    cancelled: threading.Event = field(default_factory=threading.Event)

    def check(self):
        if self.cancelled.is_set():
            raise RuntimeError("Repository operation cancelled")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Repository operation deadline exceeded")


current_execution: ContextVar[Execution | None] = ContextVar(
    "native_repository_execution", default=None
)


def checkpoint():
    execution = current_execution.get()
    if execution is not None:
        execution.check()
