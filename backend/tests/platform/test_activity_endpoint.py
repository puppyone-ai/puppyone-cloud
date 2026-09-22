"""Unit tests for the unified context-activity feed.

Covers the read-only endpoint that revives the `context_activity_items` view
(upload + import + sync_run in one shape), including the project-access
authorization boundary and the kind/active_only filters.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.platform.activity.repository import ActivityRepository
from src.platform.activity.service import ActivityService
from src.exceptions import NotFoundException
from tests.authorization_fakes import authorization_for


# ── Fake Supabase client over the view ──────────────────────────────


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._eq: dict = {}
        self._is: dict = {}
        self._limit = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def is_(self, col, val):
        self._is[col] = val
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        rows = [
            r for r in self._rows
            if all(str(r.get(c)) == str(v) for c, v in self._eq.items())
        ]
        for col, val in self._is.items():
            if val == "null":
                rows = [r for r in rows if r.get(col) is None]
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class FakeTables:
    def __init__(self, rows):
        self.rows = rows
        self.requested = None

    def table(self, name):
        self.requested = name
        return FakeQuery(self.rows)


class FakeSupabaseClient:
    def __init__(self, rows):
        self.client = FakeTables(rows)


def _rows():
    return [
        {"id": "u1", "kind": "upload", "project_id": "p1", "created_by": "user-1",
         "label": "docs/", "status": "completed", "phase": "completed", "progress": 100,
         "message": None, "error_message": None, "result_path": "docs",
         "result_commit_id": "c1", "created_at": "2026-06-03T00:00:00Z",
         "completed_at": "2026-06-03T00:01:00Z"},
        {"id": "i1", "kind": "import", "project_id": "p1", "created_by": "user-1",
         "label": "acme/repo", "status": "running", "phase": "fetching", "progress": 25,
         "message": "Fetching", "error_message": None, "result_path": None,
         "result_commit_id": None, "created_at": "2026-06-03T00:02:00Z",
         "completed_at": None},
        {"id": "s1", "kind": "sync_run", "project_id": "p1", "created_by": "user-1",
         "label": "GitHub main", "status": "running", "phase": "pull", "progress": 50,
         "message": None, "error_message": None, "result_path": None,
         "result_commit_id": None, "created_at": "2026-06-03T00:03:00Z",
         "completed_at": None},
        {"id": "s2", "kind": "sync_run", "project_id": "p1", "created_by": "user-1",
         "label": "Google Calendar", "status": "failed", "phase": "failed", "progress": 100,
         "message": None, "error_message": "source calendar selection is required",
         "result_path": None, "result_commit_id": None,
         "created_at": "2026-06-03T00:03:30Z", "completed_at": None},
        {"id": "x9", "kind": "upload", "project_id": "OTHER", "created_by": "user-2",
         "label": "leak", "status": "completed", "phase": "completed", "progress": 100,
         "message": None, "error_message": None, "result_path": None,
         "result_commit_id": None, "created_at": "2026-06-03T00:04:00Z",
         "completed_at": "2026-06-03T00:05:00Z"},
    ]


# ── Repository ──────────────────────────────────────────────────────


def test_repo_reads_the_view_scoped_to_project():
    repo = ActivityRepository(FakeSupabaseClient(_rows()))
    items = repo.list_by_project("p1")
    ids = {i.id for i in items}
    assert ids == {"u1", "i1", "s1", "s2"}        # all three kinds, project-scoped
    assert "x9" not in ids                         # other project never leaks
    assert {i.kind for i in items} == {"upload", "import", "sync_run"}


def test_repo_kind_filter():
    repo = ActivityRepository(FakeSupabaseClient(_rows()))
    items = repo.list_by_project("p1", kind="sync_run")
    assert [i.id for i in items] == ["s1", "s2"]


def test_repo_active_only_filters_to_unfinished():
    repo = ActivityRepository(FakeSupabaseClient(_rows()))
    items = repo.list_by_project("p1", active_only=True)
    # terminal rows do not remain active even if completed_at was not populated.
    assert {i.id for i in items} == {"i1", "s1"}


def test_repo_queries_the_view_not_a_table():
    fake = FakeSupabaseClient(_rows())
    ActivityRepository(fake).list_by_project("p1")
    assert fake.client.requested == "context_activity_items"


# ── Service (authorization boundary) ────────────────────────────────


def test_service_enforces_project_access_before_returning():
    svc = ActivityService(
        repo=ActivityRepository(FakeSupabaseClient(_rows())),
        authorization=authorization_for("p1"),
    )
    items = svc.list_for_project("p1", "user-1")
    assert {i.id for i in items} == {"u1", "i1", "s1", "s2"}


def test_service_denies_when_access_check_raises():
    svc = ActivityService(
        repo=ActivityRepository(FakeSupabaseClient(_rows())),
        authorization=authorization_for(),
    )
    with pytest.raises(NotFoundException):
        svc.list_for_project("p1", "intruder")


def test_service_rejects_invalid_kind():
    from fastapi import HTTPException

    svc = ActivityService(
        repo=ActivityRepository(FakeSupabaseClient(_rows())),
        authorization=authorization_for("p1"),
    )
    with pytest.raises(HTTPException) as exc:
        svc.list_for_project("p1", "user-1", kind="bogus")
    assert exc.value.status_code == 400
