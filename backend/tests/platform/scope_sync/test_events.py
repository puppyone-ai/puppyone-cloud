"""Upstream event channel (M3) tests: store, classify, service fanout + poll."""

from __future__ import annotations

from dataclasses import dataclass

from src.platform.scope_sync.events import (
    InMemoryEventStore,
    UpstreamEvent,
    classify_events,
)
from src.platform.scope_sync.policy import Persona, SyncAction, policy_for
from src.platform.scope_sync.service import ScopeSyncService
from src.platform.scope_sync.settings_store import InMemorySettingsStore


def test_inmemory_store_append_and_since():
    s = InMemoryEventStore()
    e1 = s.append(project_id="p", scope_id="x", head_version="v1",
                  affected_paths=["a"], source="publish", origin_user="u1")
    e2 = s.append(project_id="p", scope_id="x", head_version="v2",
                  affected_paths=["b"], source="publish", origin_user="u2")
    assert e1.id == 1 and e2.id == 2
    assert [e.id for e in s.since("p", "x", 0)] == [1, 2]
    assert [e.id for e in s.since("p", "x", 1)] == [2]      # cursor excludes seen
    assert s.since("p", "other", 0) == []                   # scope-scoped


def _ev(paths, origin=None):
    return UpstreamEvent(id=1, project_id="p", scope_id="x", head_version="v",
                         affected_paths=tuple(paths), origin_user=origin)


def test_classify_disjoint_integrates_overlap_holds():
    pol = policy_for(Persona.NON_DEV)
    out = classify_events([_ev(["docs/a.md"])], {"src/b.py"}, pol)
    assert out[0].actions == (SyncAction.AUTO_INTEGRATE,)
    out = classify_events([_ev(["docs/a.md"])], {"docs/a.md"}, pol)
    assert out[0].actions == (SyncAction.HOLD, SyncAction.NOTIFY)


def test_classify_skips_own_events():
    pol = policy_for(Persona.NON_DEV)
    out = classify_events([_ev(["a"], origin="me")], set(), pol, self_user="me")
    assert out == []                                        # don't react to your own publish


# ── service-level fanout + poll ───────────────────────────────────────

@dataclass
class _Scope:
    id: str
    project_id: str
    path: str


_SCOPES = [
    _Scope("s-docs", "p1", "docs"),
    _Scope("s-src", "p1", "src"),
]


def _svc():
    store = InMemoryEventStore()
    return ScopeSyncService(
        scope_lookup=lambda sid: next((s for s in _SCOPES if s.id == sid), None),
        scopes_lister=lambda pid: [(s.id, s.path) for s in _SCOPES if s.project_id == pid],
        event_store=store,
        settings_store=InMemorySettingsStore(),
    )


def test_record_publish_fans_out_path_scoped():
    svc = _svc()
    n = svc.record_publish(project_id="p1", scope_path="docs", changed_paths=["a.md"],
                           head_version="v9", origin_user="u1")
    assert n == 1                                            # docs, not sibling src
    # docs scope sees it relative; src scope sees nothing
    docs = svc.poll_events(project_id="p1", scope_id="s-docs", cursor=0)
    assert docs["events"][0]["affected_paths"] == ["a.md"] and docs["cursor"] >= 1
    assert svc.poll_events(project_id="p1", scope_id="s-src", cursor=0)["events"] == []


def test_poll_events_cursor_advances():
    svc = _svc()
    svc.record_publish(project_id="p1", scope_path="docs", changed_paths=["a.md"],
                       head_version="v1", origin_user="u1")
    first = svc.poll_events(project_id="p1", scope_id="s-docs", cursor=0)
    c = first["cursor"]
    # nothing new since cursor
    assert svc.poll_events(project_id="p1", scope_id="s-docs", cursor=c)["events"] == []


def test_activity_returns_recent_events_newest_first():
    svc = _svc()
    svc.record_publish(project_id="p1", scope_path="docs", changed_paths=["a.md"],
                       head_version="v1", origin_user="u1")
    svc.record_publish(project_id="p1", scope_path="docs", changed_paths=["b.md"],
                       head_version="v2", origin_user="u2")
    act = svc.activity(project_id="p1", scope_id="s-docs", limit=10)
    assert act["latest_head"] == "v2"                      # newest first
    assert [e["head_version"] for e in act["recent"]] == ["v2", "v1"]


def test_record_publish_is_best_effort_on_error():
    # a broken scopes_lister must not raise into the publish path
    svc = ScopeSyncService(scope_lookup=lambda sid: None,
                           scopes_lister=lambda pid: (_ for _ in ()).throw(RuntimeError("boom")),
                           event_store=InMemoryEventStore(),
                           settings_store=InMemorySettingsStore())
    assert svc.record_publish(project_id="p1", scope_path="docs", changed_paths=["a"],
                              head_version="v", origin_user=None) == 0
