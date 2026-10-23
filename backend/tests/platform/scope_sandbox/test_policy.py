"""Unit tests for the session-management policy (the core of the feature)."""

from __future__ import annotations

from src.platform.workspace.scopes.provider import SandboxState
from src.platform.workspace.scopes.registry import SandboxSession
from src.platform.workspace.scopes.policy import (
    SessionDecision,
    SessionPolicyConfig,
    adaptive_stop_timeout,
    decide,
    eviction_score,
    select_for_eviction,
)

CFG = SessionPolicyConfig()
NOW = 1_000_000.0


def _session(
    *,
    state: SandboxState = SandboxState.RUNNING,
    last_active_delta: float = 0.0,
    connected: set[str] | None = None,
    activity: list[float] | None = None,
    recent_users: dict[str, float] | None = None,
    pull_seconds: float = 0.0,
    scope_id: str = "scope-1",
) -> SandboxSession:
    return SandboxSession(
        scope_id=scope_id,
        project_id="proj",
        provider="fake",
        sandbox_id="sb-1",
        state=state,
        created_at=NOW - 10_000,
        last_active_at=NOW - last_active_delta,
        last_state_change_at=NOW - last_active_delta,
        connected_users=connected or set(),
        activity_events=activity or [],
        recent_user_events=recent_users or {},
        last_full_pull_seconds=pull_seconds,
    )


# ── decide ────────────────────────────────────────────────────────────

def test_connected_users_keep_regardless_of_idle():
    s = _session(state=SandboxState.RUNNING, last_active_delta=10 * 3600, connected={"u1"})
    assert decide(s, NOW, CFG) is SessionDecision.KEEP


def test_running_idle_below_timeout_keeps():
    s = _session(state=SandboxState.RUNNING, last_active_delta=CFG.base_idle_stop_s - 1)
    assert decide(s, NOW, CFG) is SessionDecision.KEEP


def test_running_idle_at_timeout_stops():
    s = _session(state=SandboxState.RUNNING, last_active_delta=CFG.base_idle_stop_s + 1)
    assert decide(s, NOW, CFG) is SessionDecision.STOP


def test_stopped_idle_below_destroy_keeps_stopped():
    s = _session(state=SandboxState.STOPPED, last_active_delta=CFG.idle_destroy_s - 1)
    assert decide(s, NOW, CFG) is SessionDecision.KEEP


def test_stopped_idle_past_destroy_destroys():
    s = _session(state=SandboxState.STOPPED, last_active_delta=CFG.idle_destroy_s + 1)
    assert decide(s, NOW, CFG) is SessionDecision.DESTROY


def test_destroyed_is_noop():
    s = _session(state=SandboxState.DESTROYED, last_active_delta=10 * 3600)
    assert decide(s, NOW, CFG) is SessionDecision.KEEP


def test_idle_running_first_stops_then_later_destroys():
    # An idle RUNNING box stops (keeps disk); only once STOPPED + long-idle does it destroy.
    running = _session(state=SandboxState.RUNNING, last_active_delta=CFG.base_idle_stop_s + 5)
    assert decide(running, NOW, CFG) is SessionDecision.STOP
    stopped = _session(state=SandboxState.STOPPED, last_active_delta=CFG.base_idle_stop_s + 5)
    assert decide(stopped, NOW, CFG) is SessionDecision.KEEP  # not idle long enough to destroy yet


# ── adaptive_stop_timeout ─────────────────────────────────────────────

def test_recent_users_extend_warm_window():
    base = _session()
    busy = _session(recent_users={"a": NOW, "b": NOW, "c": NOW})
    assert adaptive_stop_timeout(busy, NOW, CFG) > adaptive_stop_timeout(base, NOW, CFG)


def test_bigger_repo_extends_warm_window():
    small = _session(pull_seconds=1.0)
    big = _session(pull_seconds=60.0)
    assert adaptive_stop_timeout(big, NOW, CFG) > adaptive_stop_timeout(small, NOW, CFG)


def test_hot_scope_extends_warm_window():
    cold = _session(activity=[NOW])
    hot = _session(activity=[NOW] * (CFG.hot_frequency_threshold + 1))
    assert adaptive_stop_timeout(hot, NOW, CFG) == \
        adaptive_stop_timeout(cold, NOW, CFG) + CFG.hot_warm_bonus_s


def test_stop_timeout_capped():
    s = _session(
        recent_users={f"u{i}": NOW for i in range(100)},
        pull_seconds=10_000,
        activity=[NOW] * 100,
    )
    assert adaptive_stop_timeout(s, NOW, CFG) == CFG.max_idle_stop_s


def test_stale_users_and_activity_dont_count():
    # Events older than their windows are ignored.
    stale = _session(
        recent_users={"a": NOW - CFG.recent_user_window_s - 10},
        activity=[NOW - CFG.frequency_window_s - 10] * 10,
    )
    assert adaptive_stop_timeout(stale, NOW, CFG) == CFG.base_idle_stop_s


# ── eviction ──────────────────────────────────────────────────────────

def test_eviction_score_higher_for_busy_and_costly():
    cheap_idle = _session(scope_id="a", activity=[], recent_users={}, pull_seconds=1.0, last_active_delta=3600)
    busy_costly = _session(scope_id="b", activity=[NOW] * 5, recent_users={"x": NOW, "y": NOW}, pull_seconds=60.0, last_active_delta=0)
    assert eviction_score(busy_costly, NOW, CFG) > eviction_score(cheap_idle, NOW, CFG)


def test_eviction_decays_with_idle():
    fresh = _session(last_active_delta=0, activity=[NOW])
    stale = _session(last_active_delta=10 * 3600, activity=[NOW])
    assert eviction_score(fresh, NOW, CFG) > eviction_score(stale, NOW, CFG)


def test_select_for_eviction_picks_lowest_value_first_and_skips_connected():
    keep_connected = _session(scope_id="connected", connected={"u"}, pull_seconds=60)
    low = _session(scope_id="low", last_active_delta=8 * 3600, activity=[], pull_seconds=1.0)
    high = _session(scope_id="high", activity=[NOW] * 5, recent_users={"a": NOW}, pull_seconds=60.0)
    terminal = _session(scope_id="dead", state=SandboxState.DESTROYED)

    picked = select_for_eviction([keep_connected, low, high, terminal], NOW, CFG, count=2)
    ids = [s.scope_id for s in picked]
    assert "connected" not in ids and "dead" not in ids
    assert ids[0] == "low"          # lowest value evicted first
    assert len(ids) == 2 and ids == ["low", "high"]


def test_select_for_eviction_count_guard():
    s = _session()
    assert select_for_eviction([s], NOW, CFG, count=0) == []
    assert select_for_eviction([s], NOW, CFG, count=-1) == []
