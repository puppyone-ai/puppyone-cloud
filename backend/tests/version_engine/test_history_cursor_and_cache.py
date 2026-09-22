from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from src.version_engine.read.history_cache import HistoryGraphCache
from src.version_engine.read.history_cursor import HistoryCursorCodec
from src.version_engine.read.history_models import (
    GraphCommit,
    HistoryCursorError,
    HistoryCursorState,
    HistoryGraphSnapshot,
)


def test_history_cursor_round_trips_snapshot_and_rejects_tampering():
    codec = HistoryCursorCodec("unit-test-history-cursor-secret")
    state = HistoryCursorState(
        project_id="project-1",
        snapshot_id="1" * 64,
        roots=("a" * 40, "b" * 40),
        head_commit_id="a" * 40,
        anchor_commit_id="b" * 40,
    )

    cursor = codec.encode(state)

    assert codec.decode(cursor, project_id="project-1") == state
    with pytest.raises(HistoryCursorError, match="another project"):
        codec.decode(cursor, project_id="project-2")
    prefix, body, signature = cursor.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    with pytest.raises(HistoryCursorError, match="signature"):
        codec.decode(f"{prefix}.{body}.{replacement}{signature[1:]}", project_id="project-1")


def test_history_graph_cache_single_flights_concurrent_snapshot_builds():
    cache = HistoryGraphCache(max_snapshots=2, max_total_weight=30, ttl_seconds=60)
    builder_started = Event()
    release_builder = Event()
    snapshot = _snapshot("a")
    calls = 0

    def build():
        nonlocal calls
        calls += 1
        builder_started.set()
        assert release_builder.wait(timeout=2)
        return snapshot

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_or_build, ("p", "s"), build)
        assert builder_started.wait(timeout=2)
        second = pool.submit(cache.get_or_build, ("p", "s"), build)
        deadline = time.monotonic() + 2
        while cache.stats().waits == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert cache.stats().waits == 1
        release_builder.set()
        assert first.result(timeout=2) is snapshot
        assert second.result(timeout=2) is snapshot

    stats = cache.stats()
    assert calls == 1
    assert stats.builds == 1
    assert stats.waits == 1
    assert stats.entries == 1


def test_history_graph_cache_enforces_weight_and_ttl_bounds():
    now = [10.0]
    cache = HistoryGraphCache(
        max_snapshots=2,
        max_total_weight=6,
        ttl_seconds=5,
        clock=lambda: now[0],
    )

    cache.get_or_build(("p", "one"), lambda: _snapshot("a"))
    cache.get_or_build(("p", "two"), lambda: _snapshot("b"))
    assert cache.stats().entries == 2
    cache.get_or_build(("p", "three"), lambda: _snapshot("c"))
    assert cache.stats().entries == 2
    assert cache.stats().evictions == 1

    now[0] = 16.0
    assert cache.stats().entries == 0
    assert cache.stats().total_weight == 0


def test_history_graph_cache_weight_counts_parent_edges():
    commit_id = "d" * 40
    snapshot = HistoryGraphSnapshot(
        order=(commit_id,),
        positions={commit_id: 0},
        nodes={
            commit_id: GraphCommit(
                commit_id=commit_id,
                parent_ids=tuple(character * 40 for character in "abc"),
                tree_id="",
                author="Git",
                message="Merge",
                created_at=None,
                timestamp=0,
            ),
        },
        unreadable_commit_ids=(),
    )
    cache = HistoryGraphCache(max_snapshots=1, max_total_weight=5, ttl_seconds=60)

    assert cache.get_or_build(("p", "merge"), lambda: snapshot) is snapshot
    assert snapshot.cache_weight == 6
    assert cache.stats().entries == 0


def _snapshot(character: str) -> HistoryGraphSnapshot:
    commit_id = character * 40
    return HistoryGraphSnapshot(
        order=(commit_id,),
        positions={commit_id: 0},
        nodes={},
        unreadable_commit_ids=(),
    )
