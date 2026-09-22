"""Durable registry for ephemeral sandbox executions.

Provider SDK objects are deliberately not stored.  Each operation resolves the
provider resource from this record, so another API worker (or a restarted one)
can inspect, execute in, and stop the same sandbox.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Protocol

TABLE = "sandbox_execution_sessions"


@dataclass(slots=True)
class ExecutionSession:
    session_id: str
    provider: str
    resource_id: str
    readonly: bool
    created_at: float
    last_activity: float
    temp_path: str = ""
    project_id: str | None = None


class ExecutionSessionStore(Protocol):
    def get(self, session_id: str) -> ExecutionSession | None: ...
    def insert(self, session: ExecutionSession) -> bool: ...
    def put(self, session: ExecutionSession) -> None: ...
    def delete(self, session_id: str) -> None: ...
    def list_provider(self, provider: str) -> list[ExecutionSession]: ...


class InMemoryExecutionSessionStore:
    """Test-only implementation; production construction never selects it."""

    def __init__(self) -> None:
        self._records: dict[str, ExecutionSession] = {}

    def get(self, session_id: str) -> ExecutionSession | None:
        return self._records.get(session_id)

    def put(self, session: ExecutionSession) -> None:
        self._records[session.session_id] = session

    def insert(self, session: ExecutionSession) -> bool:
        if session.session_id in self._records:
            return False
        self._records[session.session_id] = session
        return True

    def delete(self, session_id: str) -> None:
        self._records.pop(session_id, None)

    def list_provider(self, provider: str) -> list[ExecutionSession]:
        return [row for row in self._records.values() if row.provider == provider]


class SupabaseExecutionSessionStore:
    def __init__(self, client=None) -> None:
        if client is None:
            from src.infra.supabase.client import SupabaseClient

            client = SupabaseClient().client
        self._client = client

    @staticmethod
    def _row(session: ExecutionSession) -> dict:
        row = asdict(session)
        for key in ("created_at", "last_activity"):
            row[key] = datetime.fromtimestamp(row[key], timezone.utc).isoformat()
        return row

    @staticmethod
    def _session(row: dict) -> ExecutionSession:
        def epoch(value) -> float:
            if isinstance(value, (int, float)):
                return float(value)
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()

        return ExecutionSession(
            session_id=str(row["session_id"]),
            provider=str(row["provider"]),
            resource_id=str(row["resource_id"]),
            readonly=bool(row.get("readonly")),
            created_at=epoch(row["created_at"]),
            last_activity=epoch(row["last_activity"]),
            temp_path=str(row.get("temp_path") or ""),
            project_id=str(row["project_id"]) if row.get("project_id") else None,
        )

    def get(self, session_id: str) -> ExecutionSession | None:
        response = self._client.table(TABLE).select("*").eq(
            "session_id", session_id
        ).maybe_single().execute()
        return self._session(response.data) if response.data else None

    def put(self, session: ExecutionSession) -> None:
        self._client.table(TABLE).upsert(
            self._row(session), on_conflict="session_id"
        ).execute()

    def insert(self, session: ExecutionSession) -> bool:
        try:
            self._client.table(TABLE).insert(self._row(session)).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            detail = f"{getattr(exc, 'message', '')} {exc}".lower()
            if "23505" in detail or "duplicate key" in detail:
                return False
            raise

    def delete(self, session_id: str) -> None:
        self._client.table(TABLE).delete().eq("session_id", session_id).execute()

    def list_provider(self, provider: str) -> list[ExecutionSession]:
        response = self._client.table(TABLE).select("*").eq(
            "provider", provider
        ).execute()
        return [self._session(row) for row in (response.data or [])]


def durable_execution_store() -> ExecutionSessionStore:
    return SupabaseExecutionSessionStore()
