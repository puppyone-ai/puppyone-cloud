"""Session-management policy — the core of the sandbox feature.

A scope's sandbox should stay warm when it's likely to be reused soon (so users
don't wait for a full re-pull) and be reclaimed when it isn't (to save cost).
Because the server is the git source of truth, destroying a sandbox never loses
data — the only cost of destroying is the next full pull. So the decision is a
trade-off between *warm idle cost* and *cold restart cost*, driven by metrics:

  - recency        how long since the last activity
  - frequency      how many ops in a recent window (a "hot" scope)
  - users          how many distinct users used it recently (shared scope)
  - restart cost   measured full-pull seconds (bigger repo ⇒ pricier to destroy)

Two-step reclamation mirrors the three-state model: an idle RUNNING sandbox is
first STOPPED (compute off, disk kept → cheap, fast resume), and only a
long-idle STOPPED sandbox is DESTROYED (frees disk → next use needs a full
pull). Under capacity pressure, the lowest-value sessions are evicted first.

Everything here is a pure function of (session, now, config) so it is fully
unit-testable without a provider or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.platform.workspace.scopes.provider import SandboxState
from src.platform.workspace.scopes.registry import SandboxSession


@dataclass(frozen=True)
class SessionPolicyConfig:
    # Base idle before a RUNNING sandbox is stopped (compute off, disk kept).
    base_idle_stop_s: float = 10 * 60
    # Idle before a STOPPED sandbox is destroyed (disk freed). Measured from
    # last activity, which doesn't advance while stopped, so this is "total
    # idle since last use".
    idle_destroy_s: float = 6 * 60 * 60
    # Each recent user extends the warm (stop) window — a shared scope with
    # several active users is more likely to be reused.
    per_recent_user_bonus_s: float = 5 * 60
    # Bigger repos are pricier to destroy (full pull), so keep them warm
    # longer: warm window += min(last_full_pull_seconds, cap) * weight.
    repo_pull_weight: float = 5.0
    repo_pull_cap_s: float = 120.0
    # A "hot" scope (many recent ops) gets an extra warm bonus.
    frequency_window_s: float = 30 * 60
    hot_frequency_threshold: int = 5
    hot_warm_bonus_s: float = 15 * 60
    # Window for counting "recent" distinct users.
    recent_user_window_s: float = 30 * 60
    # Hard ceiling on the adaptive stop window.
    max_idle_stop_s: float = 2 * 60 * 60


class SessionDecision(str, Enum):
    KEEP = "keep"        # leave as-is (running-and-busy, warm window, or cheap-stopped)
    STOP = "stop"        # RUNNING → STOPPED (compute off, disk kept)
    DESTROY = "destroy"  # → DESTROYED (free disk)


def adaptive_stop_timeout(session: SandboxSession, now: float, config: SessionPolicyConfig) -> float:
    """How long an idle RUNNING sandbox may stay warm before being stopped.

    Longer when: more recent users, bigger repo (pricier cold restart), or the
    scope is "hot" (frequent recent ops). Capped at ``max_idle_stop_s``.
    """
    timeout = config.base_idle_stop_s
    timeout += session.recent_user_count(now, config.recent_user_window_s) * config.per_recent_user_bonus_s
    pull = min(max(session.last_full_pull_seconds, 0.0), config.repo_pull_cap_s)
    timeout += pull * config.repo_pull_weight
    if session.activity_count(now, config.frequency_window_s) >= config.hot_frequency_threshold:
        timeout += config.hot_warm_bonus_s
    return min(timeout, config.max_idle_stop_s)


def decide(session: SandboxSession, now: float, config: SessionPolicyConfig) -> SessionDecision:
    """Lifecycle decision for one session at time ``now``."""
    # Anyone connected pins it running.
    if session.connected_users:
        return SessionDecision.KEEP

    idle = max(0.0, now - session.last_active_at)

    if session.state in (SandboxState.RUNNING, SandboxState.PENDING):
        return SessionDecision.STOP if idle >= adaptive_stop_timeout(session, now, config) else SessionDecision.KEEP

    if session.state is SandboxState.STOPPED:
        return SessionDecision.DESTROY if idle >= config.idle_destroy_s else SessionDecision.KEEP

    # DESTROYED / UNKNOWN: nothing to do.
    return SessionDecision.KEEP


def eviction_score(session: SandboxSession, now: float, config: SessionPolicyConfig) -> float:
    """Value of keeping this session warm — LOWER means evict first.

    value ∝ (recent frequency + recent users) and ∝ cold-restart cost (pricier
    to rebuild ⇒ more worth keeping), decayed by how long it's been idle.
    """
    freq = session.activity_count(now, config.frequency_window_s)
    users = session.recent_user_count(now, config.recent_user_window_s)
    cold_cost = max(session.last_full_pull_seconds, 1.0)
    idle = max(0.0, now - session.last_active_at)
    value = (freq + users + 1) * cold_cost
    return value / (1.0 + idle / 3600.0)


def select_for_eviction(
    sessions: list[SandboxSession],
    now: float,
    config: SessionPolicyConfig,
    count: int,
) -> list[SandboxSession]:
    """Pick up to ``count`` sessions to reclaim under capacity pressure.

    Connected sessions are never evicted; the rest are ordered by ascending
    value (lowest value first).
    """
    if count <= 0:
        return []
    candidates = [s for s in sessions if not s.connected_users and not s.state.is_terminal]
    candidates.sort(key=lambda s: eviction_score(s, now, config))
    return candidates[:count]
