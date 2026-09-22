"""Supabase-backed SandboxSessionStore — durable session state (roadmap #3).

Replaces the in-memory store so sessions survive restarts and are visible across
workers (required for the reaper + multi-instance to be production-correct).
One row per scope in ``scope_sandbox_sessions`` (migration
20260607000000_scope_sandbox_sessions.sql).

The (de)serialization helpers are pure and unit-tested; the store wraps the
Supabase table API (select/upsert/delete). ``put`` upserts on ``scope_id``.

Creation uses an atomic primary-key insert; losing workers adopt the winner's
row and tear down any speculative provider resource. Subsequent state updates
are idempotent upserts coordinated by the manager.
"""

from __future__ import annotations

from typing import Optional

from src.infra.supabase.client import SupabaseClient
from src.platform.scope_sandbox.provider import ConnectionInfo, SandboxState
from src.platform.scope_sandbox.registry import SandboxSession

TABLE = "scope_sandbox_sessions"


def _conn_to_dict(conn: ConnectionInfo | None) -> dict | None:
    if conn is None:
        return None
    return {
        "host": conn.host,
        "port": conn.port,
        "username": conn.username,
        "proxy_command": conn.proxy_command,
        "extra": dict(conn.extra),
    }


def _dict_to_conn(d: dict | None) -> ConnectionInfo | None:
    if not d:
        return None
    return ConnectionInfo(
        host=d.get("host", ""),
        port=int(d.get("port", 22)),
        username=d.get("username", "puppy"),
        proxy_command=d.get("proxy_command"),
        extra=dict(d.get("extra") or {}),
    )


def session_to_row(s: SandboxSession) -> dict:
    return {
        "scope_id": s.scope_id,
        "project_id": s.project_id,
        "provider": s.provider,
        "sandbox_id": s.sandbox_id,
        "state": s.state.value,
        "connected_users": sorted(s.connected_users),
        "activity_events": list(s.activity_events),
        "recent_user_events": dict(s.recent_user_events),
        "connection": _conn_to_dict(s.connection),
        "last_full_pull_seconds": s.last_full_pull_seconds,
        "repo_size_bytes": s.repo_size_bytes,
        "created_at": s.created_at,
        "last_active_at": s.last_active_at,
        "last_state_change_at": s.last_state_change_at,
    }


def row_to_session(row: dict) -> SandboxSession:
    return SandboxSession(
        scope_id=row["scope_id"],
        project_id=row["project_id"],
        provider=row["provider"],
        sandbox_id=row["sandbox_id"],
        state=SandboxState(row["state"]),
        created_at=float(row["created_at"]),
        last_active_at=float(row["last_active_at"]),
        last_state_change_at=float(row["last_state_change_at"]),
        connected_users=set(row.get("connected_users") or []),
        activity_events=[float(t) for t in (row.get("activity_events") or [])],
        recent_user_events={u: float(t) for u, t in (row.get("recent_user_events") or {}).items()},
        connection=_dict_to_conn(row.get("connection")),
        last_full_pull_seconds=float(row.get("last_full_pull_seconds") or 0.0),
        repo_size_bytes=int(row.get("repo_size_bytes") or 0),
    )


class SupabaseSandboxSessionStore:
    """Durable SandboxSessionStore over Supabase. Satisfies the store Protocol."""

    def __init__(self, supabase_client: Optional[SupabaseClient] = None) -> None:
        self._client = (supabase_client or SupabaseClient()).get_client()

    def get(self, scope_id: str) -> SandboxSession | None:
        resp = (
            self._client.table(TABLE)
            .select("*").eq("scope_id", scope_id).limit(1)
            .execute()
        )
        rows = resp.data or []
        return row_to_session(rows[0]) if rows else None

    def put(self, session: SandboxSession) -> None:
        self._client.table(TABLE).upsert(
            session_to_row(session), on_conflict="scope_id",
        ).execute()

    def insert(self, session: SandboxSession) -> bool:
        """Atomic create. The scope_id PK makes a concurrent second create fail
        with a unique violation (Postgres 23505) → return False so the caller
        adopts the winning row instead of double-creating a sandbox."""
        try:
            self._client.table(TABLE).insert(session_to_row(session)).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            msg = str(getattr(exc, "message", "")) + " " + str(exc)
            if "23505" in msg or "duplicate key" in msg.lower():
                return False
            raise

    def delete(self, scope_id: str) -> None:
        self._client.table(TABLE).delete().eq("scope_id", scope_id).execute()

    def list_all(self) -> list[SandboxSession]:
        resp = self._client.table(TABLE).select("*").execute()
        return [row_to_session(r) for r in (resp.data or [])]
