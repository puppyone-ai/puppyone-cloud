"""GAP-3 Phase 1: the version_refs store (per-scope branch/tag refs).

A ref is a named pointer to an already-promoted commit; storing one must
never touch the scope head. These tests lock in the CRUD + the
ref-name classification that keeps refs/heads/main out of the store.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.version_engine.infrastructure.supabase.version_ref_repository import (
    VersionRefStore,
    ref_type_for,
)


# ── In-memory fake Supabase ─────────────────────────────────────────


class FakeQuery:
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._op = "select"
        self._eq = {}
        self._upsert_row = None
        self._conflict = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def delete(self):
        self._op = "delete"
        return self

    def upsert(self, row, on_conflict=None):
        self._op = "upsert"
        self._upsert_row = row
        self._conflict = (on_conflict or "").split(",")
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def _match(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self._eq.items())

    def execute(self):
        rows = self._store.tables.setdefault(self._table, [])
        if self._op == "upsert":
            key = tuple(self._upsert_row.get(c) for c in self._conflict)
            for existing in rows:
                if tuple(existing.get(c) for c in self._conflict) == key:
                    existing.update(self._upsert_row)
                    return SimpleNamespace(data=[existing])
            rows.append(dict(self._upsert_row))
            return SimpleNamespace(data=[self._upsert_row])
        matched = [r for r in rows if self._match(r)]
        if self._op == "delete":
            self._store.tables[self._table] = [r for r in rows if not self._match(r)]
            return SimpleNamespace(data=matched)
        return SimpleNamespace(data=matched)


class FakeTables:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return FakeQuery(self, name)


class FakeSupabaseClient:
    def __init__(self):
        self.client = FakeTables()


class BrokenSupabaseClient:
    class _BrokenTables:
        def table(self, _name):
            raise ConnectionError("control plane unavailable")

    def __init__(self):
        self.client = self._BrokenTables()


class HistorySnapshotClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.client = self

    def rpc(self, name, args):
        self.calls.append((name, args))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.rows))


@pytest.fixture
def store():
    return VersionRefStore(client=FakeSupabaseClient())


COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


# ── ref_type_for ────────────────────────────────────────────────────


def test_ref_type_branch():
    assert ref_type_for("refs/heads/feature-x") == "branch"


def test_ref_type_tag():
    assert ref_type_for("refs/tags/v1.2.3") == "tag"


def test_ref_type_main_is_none():
    assert ref_type_for("refs/heads/main") is None


def test_ref_type_unknown_is_none():
    assert ref_type_for("refs/notes/commits") is None
    assert ref_type_for("HEAD") is None


# ── CRUD ────────────────────────────────────────────────────────────


def test_set_get_branch_ref(store):
    assert store.set_ref(
        project_id="p", scope_path="docs", ref_name="refs/heads/feat",
        commit_id=COMMIT_A, created_by="alice",
    )
    got = store.get_ref("p", "docs", "refs/heads/feat")
    assert got is not None
    assert got["commit_id"] == COMMIT_A
    assert got["ref_type"] == "branch"
    assert got["created_by"] == "alice"


def test_set_ref_upsert_updates_commit(store):
    store.set_ref(project_id="p", scope_path="", ref_name="refs/heads/feat", commit_id=COMMIT_A)
    store.set_ref(project_id="p", scope_path="", ref_name="refs/heads/feat", commit_id=COMMIT_B)
    got = store.get_ref("p", "", "refs/heads/feat")
    assert got["commit_id"] == COMMIT_B
    # still a single row
    assert len(store.list_refs("p", "")) == 1


def test_set_main_ref_refused(store):
    assert store.set_ref(
        project_id="p", scope_path="", ref_name="refs/heads/main", commit_id=COMMIT_A,
    ) is False
    assert store.list_refs("p", "") == []


def test_set_unknown_ref_refused(store):
    assert store.set_ref(
        project_id="p", scope_path="", ref_name="refs/notes/x", commit_id=COMMIT_A,
    ) is False


def test_list_refs_scoped(store):
    store.set_ref(project_id="p", scope_path="docs", ref_name="refs/heads/a", commit_id=COMMIT_A)
    store.set_ref(project_id="p", scope_path="docs", ref_name="refs/tags/v1", commit_id=COMMIT_B)
    store.set_ref(project_id="p", scope_path="src", ref_name="refs/heads/b", commit_id=COMMIT_A)
    docs = {r["ref_name"] for r in store.list_refs("p", "docs")}
    assert docs == {"refs/heads/a", "refs/tags/v1"}
    src = {r["ref_name"] for r in store.list_refs("p", "src")}
    assert src == {"refs/heads/b"}


def test_list_refs_strict_fails_closed_instead_of_hiding_branches():
    broken = VersionRefStore(client=BrokenSupabaseClient())

    assert broken.list_refs("p") == []
    with pytest.raises(RuntimeError, match="version refs are unavailable"):
        broken.list_refs("p", strict=True)


def test_project_history_refs_use_one_atomic_rpc_snapshot():
    client = HistorySnapshotClient([
        {"ref_name": "refs/heads/main", "ref_type": "branch", "commit_id": COMMIT_A},
        {"ref_name": "refs/heads/feat", "ref_type": "branch", "commit_id": COMMIT_B},
    ])
    store = VersionRefStore(client=client)

    assert store.list_project_history_refs("p") == client.rows
    assert client.calls == [
        ("get_version_project_history_refs", {"p_project_id": "p"}),
    ]


def test_project_history_ref_snapshot_fails_closed_when_rpc_is_unavailable():
    broken = VersionRefStore(client=BrokenSupabaseClient())

    with pytest.raises(RuntimeError, match="project history refs are unavailable"):
        broken.list_project_history_refs("p")


def test_delete_ref(store):
    store.set_ref(project_id="p", scope_path="", ref_name="refs/heads/feat", commit_id=COMMIT_A)
    assert store.delete_ref("p", "", "refs/heads/feat") is True
    assert store.get_ref("p", "", "refs/heads/feat") is None
    # deleting again → False (nothing removed)
    assert store.delete_ref("p", "", "refs/heads/feat") is False


def test_list_all_commit_ids_across_scopes(store):
    # GC-root source: every stored ref's commit across all scopes
    store.set_ref(project_id="p", scope_path="docs", ref_name="refs/heads/a", commit_id=COMMIT_A)
    store.set_ref(project_id="p", scope_path="src", ref_name="refs/tags/v1", commit_id=COMMIT_B)
    store.set_ref(project_id="other", scope_path="", ref_name="refs/heads/x", commit_id="e" * 40)
    ids = set(store.list_all_commit_ids("p"))
    assert ids == {COMMIT_A, COMMIT_B}            # only project p, both scopes
    assert store.list_all_commit_ids("none") == []
