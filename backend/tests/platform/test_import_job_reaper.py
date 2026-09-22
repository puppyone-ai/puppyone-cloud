"""Tests for the one-time import-job reaper (stale active-job recovery).

The reaper must NEVER fail a still-live import — a live job cannot exceed the
worker job_timeout (ARQ kills it first), so the stale window is always set above
it. These tests pin the staleness decision and the select→mark_failed wiring.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.platform.imports.repository import (
    ImportJobRepository,
    ImportJobStatus,
    _job_is_stale,
    _parse_dt,
)

UTC = timezone.utc
RUNNING = ImportJobStatus.RUNNING.value
QUEUED = ImportJobStatus.QUEUED.value
FAILED = ImportJobStatus.FAILED.value


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── _parse_dt ─────────────────────────────────────────────────────────

def test_parse_dt_tolerates_z_offset_naive_and_garbage():
    assert _parse_dt(None) is None
    assert _parse_dt("not-a-date") is None
    assert _parse_dt("2026-06-20T00:00:00Z").tzinfo is not None
    naive = _parse_dt("2026-06-20T00:00:00")
    assert naive is not None and naive.tzinfo is not None  # assumed UTC


# ── _job_is_stale (no false positives on live jobs) ───────────────────

def test_running_job_within_window_is_not_stale():
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=1)
    assert _job_is_stale(
        status=RUNNING, started_at=_iso(now - timedelta(minutes=5)),
        created_at=_iso(now - timedelta(minutes=10)), updated_at=None, cutoff=cutoff,
    ) is False


def test_running_job_past_window_is_stale():
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=1)
    assert _job_is_stale(
        status=RUNNING, started_at=_iso(now - timedelta(hours=3)),
        created_at=_iso(now - timedelta(hours=4)), updated_at=None, cutoff=cutoff,
    ) is True


def test_queued_job_judged_by_created_at():
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=1)
    assert _job_is_stale(
        status=QUEUED, started_at=None,
        created_at=_iso(now - timedelta(minutes=10)), updated_at=None, cutoff=cutoff,
    ) is False
    assert _job_is_stale(
        status=QUEUED, started_at=None,
        created_at=_iso(now - timedelta(hours=2)), updated_at=None, cutoff=cutoff,
    ) is True


def test_no_usable_timestamp_is_never_stale():
    assert _job_is_stale(
        status=RUNNING, started_at=None, created_at=None, updated_at=None,
        cutoff=datetime.now(UTC),
    ) is False


# ── recover_stale_active_jobs (select → mark_failed wiring) ───────────

class _Table:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self._op = "select"
        self._patch: dict | None = None
        self._id: str | None = None
        self._statuses: list[str] | None = None

    def select(self, *_a, **_k):
        self._op = "select"; return self

    def update(self, patch):
        self._op = "update"; self._patch = patch; return self

    def eq(self, col, val):
        if col == "id":
            self._id = val
        return self

    def in_(self, col, vals):
        if col == "status":
            self._statuses = list(vals)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self._op == "select":
            data = [dict(r) for r in self.rows
                    if self._statuses is None or r["status"] in self._statuses]
            return SimpleNamespace(data=data)
        data = []
        for r in self.rows:
            if r["id"] == self._id and (self._statuses is None or r["status"] in self._statuses):
                r.update(self._patch)
                data.append(dict(r))
        return SimpleNamespace(data=data)


class _Client:
    def __init__(self, rows): self._rows = rows
    def table(self, _name): return _Table(self._rows)


class _SB:
    def __init__(self, rows): self.client = _Client(rows)


def _row(job_id: str, status: str, *, started_delta=None, created_delta) -> dict:
    now = datetime.now(UTC)
    return {
        "id": job_id, "project_id": "p", "created_by": "u",
        "provider": "github", "source_url": "https://x", "status": status,
        "started_at": _iso(now - started_delta) if started_delta else None,
        "created_at": _iso(now - created_delta),
        "updated_at": _iso(now - created_delta),
    }


async def test_recover_reaps_only_stale_active_jobs():
    rows = [
        _row("stale-run", RUNNING, started_delta=timedelta(hours=3), created_delta=timedelta(hours=4)),
        _row("fresh-run", RUNNING, started_delta=timedelta(minutes=5), created_delta=timedelta(minutes=10)),
        _row("stale-queue", QUEUED, created_delta=timedelta(hours=2)),
        _row("done", ImportJobStatus.COMPLETED.value, created_delta=timedelta(hours=5)),
    ]
    repo = ImportJobRepository(_SB(rows))
    recovered = repo.recover_stale_active_jobs(stale_seconds=3600, limit=100)
    ids = {j.id for j in recovered}
    assert ids == {"stale-run", "stale-queue"}     # dead-worker orphans reaped
    assert all(j.status == FAILED for j in recovered)
    by_id = {r["id"]: r for r in rows}
    assert by_id["fresh-run"]["status"] == RUNNING  # live job untouched
    assert by_id["done"]["status"] == ImportJobStatus.COMPLETED.value
