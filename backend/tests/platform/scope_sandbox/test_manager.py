"""ScopeSandboxManager flow tests against a FakeProvider + in-memory store."""

from __future__ import annotations

from src.platform.workspace.scopes.manager import AcquiredVia, ScopeSandboxManager
from src.platform.workspace.scopes.policy import SessionPolicyConfig
from src.platform.workspace.scopes.provider import (
    ConnectionInfo,
    ProviderCapabilities,
    SandboxInfo,
    SandboxProvider,
    SandboxSpec,
    SandboxState,
)
from src.platform.workspace.scopes.registry import InMemorySandboxSessionStore, SandboxSession


class FakeProvider(SandboxProvider):
    def __init__(self, *, supports_stop_resume: bool = True) -> None:
        self._caps = ProviderCapabilities(
            name="fake",
            supports_stop_resume=supports_stop_resume,
            supports_destroy=True,
            supports_tcp_ingress=True,
        )
        self.calls: list[tuple[str, str]] = []
        self.fail_on: set[str] = set()
        self._counter = 0
        self.states: dict[str, SandboxState] = {}

    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    def _conn(self, sid: str) -> ConnectionInfo:
        return ConnectionInfo(host=f"{sid}.fake", port=22)

    async def create(self, spec: SandboxSpec) -> SandboxInfo:
        if "create" in self.fail_on:
            raise RuntimeError("create boom")
        self._counter += 1
        sid = f"sb-{self._counter}"
        self.calls.append(("create", sid))
        self.states[sid] = SandboxState.RUNNING
        return SandboxInfo(sid, SandboxState.RUNNING, self._conn(sid))

    async def start(self, sandbox_id: str) -> SandboxInfo:
        if "start" in self.fail_on:
            raise RuntimeError("start boom")
        self.calls.append(("start", sandbox_id))
        self.states[sandbox_id] = SandboxState.RUNNING
        return SandboxInfo(sandbox_id, SandboxState.RUNNING, self._conn(sandbox_id))

    async def stop(self, sandbox_id: str) -> SandboxInfo:
        if "stop" in self.fail_on:
            raise RuntimeError("stop boom")
        self.calls.append(("stop", sandbox_id))
        self.states[sandbox_id] = SandboxState.STOPPED
        return SandboxInfo(sandbox_id, SandboxState.STOPPED)

    async def destroy(self, sandbox_id: str) -> None:
        if "destroy" in self.fail_on:
            raise RuntimeError("destroy boom")
        self.calls.append(("destroy", sandbox_id))
        self.states[sandbox_id] = SandboxState.DESTROYED

    async def status(self, sandbox_id: str) -> SandboxInfo:
        if "status" in self.fail_on:
            raise RuntimeError("status boom")
        return SandboxInfo(sandbox_id, self.states.get(sandbox_id, SandboxState.UNKNOWN))

    async def extend(self, sandbox_id: str) -> None:
        self.calls.append(("extend", sandbox_id))

    def count(self, op: str) -> int:
        return sum(1 for o, _ in self.calls if o == op)


# Deterministic policy: stop after 100s idle, destroy after 1000s idle; no
# user/repo/hot extensions so the adaptive timeout is exactly base_idle_stop_s.
CFG = SessionPolicyConfig(
    base_idle_stop_s=100,
    idle_destroy_s=1000,
    per_recent_user_bonus_s=0,
    repo_pull_weight=0,
    hot_warm_bonus_s=0,
)


def _mgr(provider=None):
    provider = provider or FakeProvider()
    return ScopeSandboxManager(provider, InMemorySandboxSessionStore(), CFG), provider


def _spec(scope_id="scope-1"):
    return SandboxSpec(scope_id=scope_id, project_id="proj")


async def test_acquire_creates_when_none():
    mgr, prov = _mgr()
    res = await mgr.acquire(_spec(), "u1", now=0)
    assert res.via is AcquiredVia.CREATED
    assert res.session.state is SandboxState.RUNNING
    assert res.session.connection is not None
    assert prov.count("create") == 1


async def test_acquire_reuses_running_sandbox_warm_hit():
    mgr, prov = _mgr()
    await mgr.acquire(_spec(), "u1", now=0)
    res = await mgr.acquire(_spec(), "u2", now=10)
    assert res.via is AcquiredVia.REUSED
    assert prov.count("create") == 1            # no second create
    assert res.session.connected_users == {"u1", "u2"}  # shared across users


async def test_acquire_resumes_stopped_sandbox():
    mgr, prov = _mgr()
    await mgr.acquire(_spec(), "u1", now=0)
    await mgr.release("scope-1", "u1", now=10)
    await mgr.reap(now=200)                      # idle > 100 → STOP
    assert prov.count("stop") == 1
    res = await mgr.acquire(_spec(), "u1", now=300)
    assert res.via is AcquiredVia.RESUMED
    assert prov.count("start") == 1
    assert prov.count("create") == 1             # not recreated


async def test_reap_stop_then_destroy_two_step():
    mgr, prov = _mgr()
    await mgr.acquire(_spec(), "u1", now=0)
    await mgr.release("scope-1", "u1", now=0)
    # idle 200 > stop(100) → STOP (disk kept)
    s1 = await mgr.reap(now=200)
    assert (s1.stopped, s1.destroyed) == (1, 0)
    # still idle since last_active(0); 1500 > destroy(1000) → DESTROY
    s2 = await mgr.reap(now=1500)
    assert (s2.stopped, s2.destroyed) == (0, 1)
    assert prov.count("destroy") == 1
    assert mgr._store.get("scope-1") is None     # record removed


async def test_reap_keeps_connected_and_recent():
    mgr, _ = _mgr()
    await mgr.acquire(_spec(), "u1", now=0)       # still connected
    s = await mgr.reap(now=100_000)
    assert (s.kept, s.stopped, s.destroyed) == (1, 0, 0)


async def test_release_does_not_stop_immediately():
    mgr, prov = _mgr()
    await mgr.acquire(_spec(), "u1", now=0)
    await mgr.release("scope-1", "u1", now=10)
    # within warm window → reap keeps it running
    s = await mgr.reap(now=50)
    assert s.kept == 1 and prov.count("stop") == 0


async def test_reap_error_counted_and_sweep_continues():
    prov = FakeProvider()
    prov.fail_on.add("stop")
    mgr, _ = _mgr(prov)
    await mgr.acquire(_spec("a"), "u", now=0)
    await mgr.acquire(_spec("b"), "u", now=0)
    await mgr.release("a", "u", now=0)
    await mgr.release("b", "u", now=0)
    summary = await mgr.reap(now=500)
    assert summary.errors == 2 and summary.stopped == 0  # both stops failed, sweep finished


async def test_provider_without_stop_resume_destroys_on_stop_decision():
    prov = FakeProvider(supports_stop_resume=False)
    mgr, _ = _mgr(prov)
    await mgr.acquire(_spec(), "u1", now=0)
    await mgr.release("scope-1", "u1", now=0)
    summary = await mgr.reap(now=200)            # STOP decided, but can't stop → destroy
    assert summary.stopped == 1                  # counted as a stop action
    assert prov.count("destroy") == 1 and prov.count("stop") == 0
    assert mgr._store.get("scope-1") is None


async def test_revoke_user_offboarding():
    mgr, _ = _mgr()
    await mgr.acquire(_spec(), "u1", now=0)
    await mgr.acquire(_spec(), "u2", now=0)
    remaining = await mgr.revoke_user("scope-1", "u1")
    assert remaining == 1
    session = mgr._store.get("scope-1")
    assert session.connected_users == {"u2"} and "u1" not in session.recent_user_events


async def test_revoke_user_invokes_revoke_hook():
    prov = FakeProvider()
    seen: list[tuple[str, str]] = []

    async def revoke_hook(provider, sandbox_id, user_id):
        seen.append((sandbox_id, user_id))

    mgr = ScopeSandboxManager(
        prov, InMemorySandboxSessionStore(), CFG, revoke_hook=revoke_hook
    )
    res = await mgr.acquire(_spec(), "u1", now=0)
    await mgr.revoke_user("scope-1", "u1")
    assert seen == [(res.session.sandbox_id, "u1")]  # SSH access pulled on offboarding


async def test_revoke_user_survives_hook_failure():
    prov = FakeProvider()

    async def boom(provider, sandbox_id, user_id):
        raise RuntimeError("ssh box unreachable")

    mgr = ScopeSandboxManager(prov, InMemorySandboxSessionStore(), CFG, revoke_hook=boom)
    await mgr.acquire(_spec(), "u1", now=0)
    await mgr.acquire(_spec(), "u2", now=0)
    remaining = await mgr.revoke_user("scope-1", "u1")  # best-effort: still drops tracking
    assert remaining == 1
    assert mgr._store.get("scope-1").connected_users == {"u2"}


async def test_kill_scope_destroys_and_drops_record():
    mgr, prov = _mgr()
    await mgr.acquire(_spec(), "u1", now=0)
    assert await mgr.kill_scope("scope-1") is True
    assert prov.count("destroy") == 1
    assert mgr._store.get("scope-1") is None
    assert await mgr.kill_scope("scope-1") is False  # idempotent


async def test_acquire_recreates_after_destroy():
    mgr, prov = _mgr()
    await mgr.acquire(_spec(), "u1", now=0)
    await mgr.kill_scope("scope-1")
    res = await mgr.acquire(_spec(), "u1", now=10)
    assert res.via is AcquiredVia.CREATED and prov.count("create") == 2


async def test_evict_to_capacity_stops_lowest_value():
    mgr, prov = _mgr()
    # Three idle running scopes; differentiate value by pull cost.
    for sid, pull in (("cheap", 1.0), ("mid", 30.0), ("dear", 90.0)):
        await mgr.acquire(_spec(sid), "u", now=0)
        await mgr.release(sid, "u", now=0)
        mgr.record_pull_cost(sid, pull)
    evicted = await mgr.evict_to_capacity(max_active=1, now=10)
    # over capacity by 2 → the two cheapest-to-rebuild evicted, "dear" kept warm
    assert set(evicted) == {"cheap", "mid"}
    assert prov.count("stop") == 2


async def test_acquire_runs_bootstrap_only_on_create():
    prov = FakeProvider()
    calls: list[str] = []
    async def bootstrap(provider, sandbox_id, spec):
        calls.append(sandbox_id)
    mgr = ScopeSandboxManager(prov, InMemorySandboxSessionStore(), CFG, bootstrap=bootstrap)
    await mgr.acquire(_spec(), "u1", now=0)          # CREATED → bootstrap runs
    await mgr.acquire(_spec(), "u2", now=10)         # REUSED → no bootstrap
    assert len(calls) == 1                            # provisioned exactly once
    await mgr.release("scope-1", "u1", now=10); await mgr.release("scope-1", "u2", now=10)
    await mgr.reap(now=200)                           # STOP
    await mgr.acquire(_spec(), "u1", now=300)        # RESUMED → no bootstrap (disk kept)
    assert len(calls) == 1


async def test_acquire_extends_provider_timeout():
    mgr, prov = _mgr()
    await mgr.acquire(_spec(), "u1", now=0)
    assert prov.count("extend") == 1                  # active session keeps its box alive


async def test_reuse_reconciles_when_stale_and_box_was_stopped_out_of_band():
    # reconcile_after_s small so a stale REUSE re-verifies the provider.
    prov = FakeProvider()
    mgr = ScopeSandboxManager(prov, InMemorySandboxSessionStore(), CFG, reconcile_after_s=50)
    r1 = await mgr.acquire(_spec(), "u1", now=0)
    sid = r1.session.sandbox_id
    # provider stopped it out-of-band (e.g. its own timeout); registry still says RUNNING
    prov.states[sid] = SandboxState.STOPPED
    r2 = await mgr.acquire(_spec(), "u2", now=1000)   # idle 1000 > 50 → reconcile
    assert r2.via is AcquiredVia.RESUMED and prov.count("start") == 1


async def test_reuse_reconciles_when_box_was_killed_out_of_band():
    prov = FakeProvider()
    mgr = ScopeSandboxManager(prov, InMemorySandboxSessionStore(), CFG, reconcile_after_s=50)
    r1 = await mgr.acquire(_spec(), "u1", now=0)
    prov.states[r1.session.sandbox_id] = SandboxState.DESTROYED
    r2 = await mgr.acquire(_spec(), "u2", now=1000)   # gone → recreate
    assert r2.via is AcquiredVia.CREATED and prov.count("create") == 2


async def test_reuse_stays_fast_when_recently_active():
    prov = FakeProvider()
    mgr = ScopeSandboxManager(prov, InMemorySandboxSessionStore(), CFG, reconcile_after_s=50)
    await mgr.acquire(_spec(), "u1", now=0)
    r = await mgr.acquire(_spec(), "u2", now=10)      # idle 10 <= 50 → no status call
    assert r.via is AcquiredVia.REUSED and prov.count("status") == 0


async def test_record_pull_cost_extends_warm_window():
    # A scope with a big measured pull should survive a reap that would stop a cheap one.
    cfg = SessionPolicyConfig(
        base_idle_stop_s=100, idle_destroy_s=1000,
        per_recent_user_bonus_s=0, hot_warm_bonus_s=0,
        repo_pull_weight=5.0, repo_pull_cap_s=120.0,
    )
    prov = FakeProvider()
    mgr = ScopeSandboxManager(prov, InMemorySandboxSessionStore(), cfg)
    await mgr.acquire(SandboxSpec(scope_id="big", project_id="p"), "u", now=0)
    await mgr.release("big", "u", now=0)
    mgr.record_pull_cost("big", 60.0)            # warm window = 100 + 60*5 = 400
    s = await mgr.reap(now=300)                   # 300 < 400 → kept
    assert s.kept == 1 and prov.count("stop") == 0
    s2 = await mgr.reap(now=500)                  # 500 > 400 → stopped
    assert s2.stopped == 1


async def test_create_race_adopts_winner_and_reclaims_duplicate():
    # Simulate another instance having already created the scope's sandbox: the
    # store.insert claim fails, so we destroy our just-created box and adopt the
    # winner instead of leaving two active sandboxes.
    prov = FakeProvider()
    store = InMemorySandboxSessionStore()
    mgr = ScopeSandboxManager(prov, store, CFG)
    store.put(SandboxSession(
        scope_id="scope-1", project_id="proj", provider="fake",
        sandbox_id="winner-sb", state=SandboxState.RUNNING,
        created_at=0, last_active_at=0, last_state_change_at=0,
    ))
    session, via = await mgr._create_session(_spec(), now=0)
    assert via is AcquiredVia.REUSED
    assert session.sandbox_id == "winner-sb"      # adopted the winner
    assert prov.count("create") == 1              # we created one…
    assert prov.count("destroy") == 1             # …then reclaimed the duplicate


async def test_create_race_resumes_stopped_winner():
    prov = FakeProvider()
    store = InMemorySandboxSessionStore()
    mgr = ScopeSandboxManager(prov, store, CFG)
    store.put(SandboxSession(
        scope_id="scope-1", project_id="proj", provider="fake",
        sandbox_id="winner-sb", state=SandboxState.STOPPED,
        created_at=0, last_active_at=0, last_state_change_at=0,
    ))
    _, via = await mgr._create_session(_spec(), now=0)
    assert via is AcquiredVia.RESUMED and prov.count("start") == 1
    assert prov.count("destroy") == 1             # our duplicate reclaimed


async def test_reap_only_provider_skips_other_providers():
    prov = FakeProvider()                          # capabilities().name == "fake"
    store = InMemorySandboxSessionStore()
    mgr = ScopeSandboxManager(prov, store, CFG)
    await mgr.acquire(_spec("a"), "u", now=0)
    await mgr.release("a", "u", now=0)
    # a session owned by a DIFFERENT provider sharing the store
    store.put(SandboxSession(
        scope_id="b", project_id="p", provider="other",
        sandbox_id="o", state=SandboxState.RUNNING,
        created_at=0, last_active_at=0, last_state_change_at=0,
    ))
    summary = await mgr.reap(now=500, only_provider="fake")
    assert summary.stopped == 1                    # only the "fake" session acted on
    assert store.get("b") is not None              # "other" provider session untouched
