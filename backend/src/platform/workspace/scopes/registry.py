"""Durable-ish session state for scope-keyed sandboxes.

The session manager must survive process restarts and coordinate across
workers, so session state is kept OUTSIDE the provider (the provider is a
stateless API wrapper). This module defines the record + a storage protocol;
the in-memory implementation is for tests and single-process dev. A
DB/Redis-backed implementation (production) just has to satisfy
:class:`SandboxSessionStore` — see the methods' contracts.

One record per scope (a scope shares a single sandbox across its users).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from src.platform.workspace.scopes.provider import ConnectionInfo, SandboxState


@dataclass
class SandboxSession:
    """All state the session-management policy needs for one scope's sandbox.

    Time fields are epoch seconds. Activity is tracked as a capped list of
    timestamps so the policy can compute a frequency over a window without a
    background decay job.
    """

    scope_id: str
    project_id: str
    provider: str
    sandbox_id: str
    state: SandboxState
    created_at: float
    last_active_at: float
    last_state_change_at: float
    # Users currently holding an SSH session (a non-empty set pins RUNNING).
    connected_users: set[str] = field(default_factory=set)
    # Recent activity event timestamps (pruned to the frequency window).
    activity_events: list[float] = field(default_factory=list)
    # Recent distinct users (pruned to the recency window) — more users ⇒ keep warm longer.
    recent_user_events: dict[str, float] = field(default_factory=dict)
    connection: ConnectionInfo | None = None
    # Cold-restart cost signal: measured seconds of the last full pull (bigger
    # repo ⇒ pricier to destroy ⇒ keep warm longer).
    last_full_pull_seconds: float = 0.0
    repo_size_bytes: int = 0

    def recent_user_count(self, now: float, window_s: float) -> int:
        return sum(1 for ts in self.recent_user_events.values() if now - ts <= window_s)

    def activity_count(self, now: float, window_s: float) -> int:
        return sum(1 for ts in self.activity_events if now - ts <= window_s)


class SandboxSessionStore(Protocol):
    """Storage for one-session-per-scope. Implementations must be safe for the
    manager's read-modify-write; a DB impl should use row locking / upsert."""

    def get(self, scope_id: str) -> SandboxSession | None: ...
    def put(self, session: SandboxSession) -> None: ...
    def delete(self, scope_id: str) -> None: ...
    def list_all(self) -> list[SandboxSession]: ...

    def insert(self, session: SandboxSession) -> bool:
        """Atomically create the row for ``session.scope_id``. Returns False if a
        row already exists (another writer won the create race) — the basis for
        one-sandbox-per-scope across instances. ``put`` remains an upsert for
        subsequent updates."""
        ...


class InMemorySandboxSessionStore:
    """Process-local store for tests / single-process dev."""

    def __init__(self) -> None:
        self._sessions: dict[str, SandboxSession] = {}

    def get(self, scope_id: str) -> SandboxSession | None:
        return self._sessions.get(scope_id)

    def put(self, session: SandboxSession) -> None:
        self._sessions[session.scope_id] = session

    def insert(self, session: SandboxSession) -> bool:
        if session.scope_id in self._sessions:
            return False
        self._sessions[session.scope_id] = session
        return True

    def delete(self, scope_id: str) -> None:
        self._sessions.pop(scope_id, None)

    def list_all(self) -> list[SandboxSession]:
        return list(self._sessions.values())
