"""Tests for the Supabase-backed session store (serialization + CRUD + manager wiring).

Uses an in-memory fake of the Supabase table API (no DB), so the store's logic +
(de)serialization are exercised without a live database.
"""

from __future__ import annotations

from src.platform.workspace.scopes.manager import AcquiredVia, ScopeSandboxManager
from src.platform.workspace.scopes.provider import ConnectionInfo, SandboxState
from src.platform.workspace.scopes.registry import SandboxSession
from src.platform.workspace.scopes.supabase_store import (
    SupabaseSandboxSessionStore,
    row_to_session,
    session_to_row,
)
from tests.platform.scope_sandbox.test_manager import CFG, FakeProvider, _spec


# ── minimal in-memory fake of the supabase table query API ────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows: dict):
        self._rows = rows
        self._op = "select"
        self._payload = None
        self._filters: dict = {}
        self._limit = None

    def select(self, *_):
        self._op = "select"; return self

    def upsert(self, payload, on_conflict=None):
        self._op = "upsert"; self._payload = payload; return self

    def insert(self, payload):
        self._op = "insert"; self._payload = payload; return self

    def delete(self):
        self._op = "delete"; return self

    def eq(self, k, v):
        self._filters[k] = v; return self

    def limit(self, n):
        self._limit = n; return self

    def _match(self, row):
        return all(row.get(k) == v for k, v in self._filters.items())

    def execute(self):
        if self._op == "upsert":
            self._rows[self._payload["scope_id"]] = dict(self._payload)
            return _Resp([dict(self._payload)])
        if self._op == "insert":
            sid = self._payload["scope_id"]
            if sid in self._rows:  # simulate Postgres PK unique violation
                raise Exception('duplicate key value violates unique constraint (23505)')
            self._rows[sid] = dict(self._payload)
            return _Resp([dict(self._payload)])
        if self._op == "delete":
            hit = [sid for sid, r in list(self._rows.items()) if self._match(r)]
            for sid in hit:
                del self._rows[sid]
            return _Resp([{"scope_id": sid} for sid in hit])
        out = [r for r in self._rows.values() if self._match(r)]
        if self._limit is not None:
            out = out[: self._limit]
        return _Resp([dict(r) for r in out])


class _Client:
    def __init__(self):
        self.rows: dict = {}

    def table(self, _name):
        return _Query(self.rows)


class FakeSupabase:
    def __init__(self):
        self._c = _Client()

    def get_client(self):
        return self._c


def _full_session() -> SandboxSession:
    return SandboxSession(
        scope_id="s1", project_id="p1", provider="e2b", sandbox_id="sb-1",
        state=SandboxState.RUNNING,
        created_at=100.5, last_active_at=200.25, last_state_change_at=150.0,
        connected_users={"alice", "bob"},
        activity_events=[100.0, 200.25],
        recent_user_events={"alice": 200.25, "bob": 150.0},
        connection=ConnectionInfo(host="8081-x.e2b.app", port=22, username="user",
                                  proxy_command="websocat - wss://x", extra={"wss_url": "wss://x"}),
        last_full_pull_seconds=42.0, repo_size_bytes=1234,
    )


def test_row_roundtrip_preserves_all_fields():
    s = _full_session()
    back = row_to_session(session_to_row(s))
    assert back == s   # dataclass eq across all fields incl set/dict/connection


def test_roundtrip_without_connection():
    s = _full_session()
    s.connection = None
    assert row_to_session(session_to_row(s)).connection is None


def test_store_crud():
    store = SupabaseSandboxSessionStore(FakeSupabase())
    assert store.get("s1") is None
    s = _full_session()
    store.put(s)
    got = store.get("s1")
    assert got == s
    assert [x.scope_id for x in store.list_all()] == ["s1"]
    store.delete("s1")
    assert store.get("s1") is None and store.list_all() == []


def test_store_put_is_upsert():
    store = SupabaseSandboxSessionStore(FakeSupabase())
    s = _full_session()
    store.put(s)
    s.state = SandboxState.STOPPED
    store.put(s)                       # second put updates, not duplicates
    assert len(store.list_all()) == 1
    assert store.get("s1").state is SandboxState.STOPPED


def test_store_insert_is_atomic_create():
    store = SupabaseSandboxSessionStore(FakeSupabase())
    s = _full_session()
    assert store.insert(s) is True            # first create wins
    assert store.insert(s) is False           # PK conflict → caller adopts winner
    assert len(store.list_all()) == 1


async def test_manager_works_on_supabase_store():
    # The manager (acquire → reap → resume) operates correctly on the DB-backed
    # store exactly as on the in-memory one — i.e. session state is durable.
    store = SupabaseSandboxSessionStore(FakeSupabase())
    prov = FakeProvider()
    mgr = ScopeSandboxManager(prov, store, CFG)
    r = await mgr.acquire(_spec(), "alice", now=0)
    assert r.via is AcquiredVia.CREATED and store.get("scope-1") is not None
    await mgr.release("scope-1", "alice", now=0)
    summary = await mgr.reap(now=200)            # idle → STOP, persisted
    assert summary.stopped == 1 and store.get("scope-1").state is SandboxState.STOPPED
    r2 = await mgr.acquire(_spec(), "alice", now=300)
    assert r2.via is AcquiredVia.RESUMED
