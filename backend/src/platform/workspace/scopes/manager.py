"""ScopeSandboxManager — ties provider + session store + policy together.

One sandbox per scope, shared across the scope's users. The manager owns the
warm/cold lifecycle:

  - ``acquire`` ensures a RUNNING sandbox for a (scope, user) and reports
    whether that was a warm hit (REUSED), a cheap resume (RESUMED, disk kept),
    or a cold create (CREATED, full pull) — the metric that justifies keeping
    sandboxes warm.
  - ``touch`` records activity (drives the policy's recency/frequency signals).
  - ``release`` drops a user's connection (does not stop immediately — the
    reaper decides based on the policy).
  - ``reap`` applies the policy across all sessions (RUNNING-idle → STOP,
    STOPPED-long-idle → DESTROY).
  - ``revoke_user`` (offboarding) and ``kill_scope`` (admin) for governance.
  - ``evict_to_capacity`` reclaims lowest-value sessions under pressure.

Per-scope async locks serialize lifecycle ops so two concurrent acquires can't
double-create. Durable state lives in the injected store.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

from src.platform.workspace.scopes.policy import (
    SessionDecision,
    SessionPolicyConfig,
    decide,
    select_for_eviction,
)
from src.platform.workspace.scopes.provider import (
    SandboxProvider,
    SandboxSpec,
    SandboxState,
)
from src.platform.workspace.scopes.registry import SandboxSession, SandboxSessionStore
from src.utils.logger import log_info, log_warning


class AcquiredVia(str, Enum):
    REUSED = "reused"    # already RUNNING — warm hit, ~0 latency
    RESUMED = "resumed"  # was STOPPED → start, disk kept → incremental fetch
    CREATED = "created"  # none/destroyed → create — cold, full pull


@dataclass
class AcquireResult:
    session: SandboxSession
    via: AcquiredVia


@dataclass
class ReapSummary:
    kept: int = 0
    stopped: int = 0
    destroyed: int = 0
    errors: int = 0


class ScopeSandboxManager:
    def __init__(
        self,
        provider: SandboxProvider,
        store: SandboxSessionStore,
        config: SessionPolicyConfig | None = None,
        *,
        clock: Callable[[], float] = time.time,
        bootstrap: "Callable[[SandboxProvider, str, SandboxSpec], Awaitable[None]] | None" = None,
        revoke_hook: "Callable[[SandboxProvider, str, str], Awaitable[None]] | None" = None,
        reconcile_after_s: float = 60.0,
    ) -> None:
        self._provider = provider
        self._store = store
        self._config = config or SessionPolicyConfig()
        self._clock = clock
        # Called once after a sandbox is CREATED (cold path) to provision the
        # scope workspace (clone + git config). See scope_provision.
        self._bootstrap = bootstrap
        # Called on revoke_user(provider, sandbox_id, user_id) so offboarding
        # also pulls the user's SSH access from the box. See ssh_credentials.
        self._revoke_hook = revoke_hook
        # On a warm REUSE, only re-verify the provider's real state when the
        # session has been idle longer than this (a stale record may have been
        # stopped/killed out-of-band, e.g. E2B's own timeout). Recent activity
        # ⇒ trust RUNNING and keep the warm hit instant.
        self._reconcile_after_s = reconcile_after_s
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @property
    def provider(self) -> SandboxProvider:
        return self._provider

    def _lock(self, scope_id: str) -> asyncio.Lock:
        return self._locks[scope_id]

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else now

    # ── activity bookkeeping (drives policy signals) ──────────────────

    def _touch(self, session: SandboxSession, user_id: str, now: float) -> None:
        session.last_active_at = now
        session.activity_events.append(now)
        # Prune to the frequency window (keep a little slack so the count is stable).
        window = self._config.frequency_window_s
        session.activity_events = [t for t in session.activity_events if now - t <= window]
        session.recent_user_events[user_id] = now
        ruw = self._config.recent_user_window_s
        session.recent_user_events = {
            u: t for u, t in session.recent_user_events.items() if now - t <= ruw
        }

    # ── acquire (the warm/cold path) ──────────────────────────────────

    async def acquire(
        self,
        spec: SandboxSpec,
        user_id: str,
        *,
        now: float | None = None,
    ) -> AcquireResult:
        scope_id = spec.scope_id
        async with self._lock(scope_id):
            now = self._now(now)
            session = self._store.get(scope_id)

            if session is None or session.state in (SandboxState.DESTROYED, SandboxState.UNKNOWN):
                session, via = await self._create_session(spec, now, prev=session)
            elif session.state is SandboxState.STOPPED:
                session, via = await self._resume_session(session, now)
            else:  # RUNNING / PENDING
                session, via = await self._reuse_or_reconcile(session, spec, now)

            session.connected_users.add(user_id)
            self._touch(session, user_id, now)
            await self._extend(session)
            self._store.put(session)
            return AcquireResult(session=session, via=via)

    def _new_session(self, spec, now, info, prev=None) -> SandboxSession:
        return SandboxSession(
            scope_id=spec.scope_id,
            project_id=spec.project_id,
            provider=self._provider.capabilities().name,
            sandbox_id=info.sandbox_id,
            state=info.state,
            created_at=now,
            last_active_at=now,
            last_state_change_at=now,
            connection=info.connection,
            # carry forward usage signals if we're recreating a dropped session
            connected_users=prev.connected_users if prev else set(),
            last_full_pull_seconds=prev.last_full_pull_seconds if prev else 0.0,
        )

    async def _create_session(self, spec, now, prev=None):
        info = await self._provider.create(spec)
        session = self._new_session(spec, now, info, prev)

        # Recreating over a row we already own (reconcile found the box gone, or a
        # DESTROYED record): we hold the per-scope lock and the row exists, so just
        # replace it — the cross-instance create-race below is only for a brand-new
        # scope (no row yet).
        if prev is not None:
            self._store.put(session)
            if self._bootstrap is not None:
                await self._bootstrap(self._provider, session.sandbox_id, spec)
            return session, AcquiredVia.CREATED

        # Brand-new scope. Atomic claim: across instances the scope_id PK lets only
        # ONE writer win the create. If we lose, discard our just-created box and
        # adopt the winner's (no double-active sandbox, no leak). Single-instance
        # always wins here (the per-scope asyncio lock already serialized us).
        if self._store.insert(session):
            if self._bootstrap is not None:
                await self._bootstrap(self._provider, session.sandbox_id, spec)
            return session, AcquiredVia.CREATED

        log_warning(
            f"[scope-sandbox] create race scope={spec.scope_id}; "
            f"reclaiming duplicate sandbox {session.sandbox_id}"
        )
        try:
            await self._provider.destroy(session.sandbox_id)
        except Exception as exc:  # noqa: BLE001 - reclaim is best-effort
            log_warning(f"[scope-sandbox] reclaim destroy failed scope={spec.scope_id}: {exc}")

        winner = self._store.get(spec.scope_id)
        if winner is None or winner.state is SandboxState.DESTROYED:
            # Winner vanished between insert-fail and read (extremely rare):
            # provision fresh and upsert.
            info = await self._provider.create(spec)
            session = self._new_session(spec, now, info, prev)
            if self._bootstrap is not None:
                await self._bootstrap(self._provider, session.sandbox_id, spec)
            self._store.put(session)
            return session, AcquiredVia.CREATED
        if winner.state is SandboxState.STOPPED:
            return await self._resume_session(winner, now)
        return winner, AcquiredVia.REUSED

    async def _resume_session(self, session, now):
        info = await self._provider.start(session.sandbox_id)
        session.state = info.state
        session.connection = info.connection or session.connection
        session.last_state_change_at = now
        return session, AcquiredVia.RESUMED

    async def _reuse_or_reconcile(self, session, spec, now):
        idle = max(0.0, now - session.last_active_at)
        if idle <= self._reconcile_after_s:
            return session, AcquiredVia.REUSED  # fresh activity ⇒ trust it (fast warm hit)
        # Stale record: verify the provider's real state before reusing, so an
        # out-of-band stop/kill (e.g. E2B's own timeout) can't hand back a dead box.
        info = await self._provider.status(session.sandbox_id)
        if info.state in (SandboxState.RUNNING, SandboxState.PENDING):
            return session, AcquiredVia.REUSED
        if info.state is SandboxState.STOPPED:
            return await self._resume_session(session, now)
        return await self._create_session(spec, now, prev=session)  # gone ⇒ recreate

    async def _extend(self, session: SandboxSession) -> None:
        """Best-effort: push out the provider's own idle/auto-stop timeout so an
        active session never gets reclaimed underneath the manager."""
        try:
            await self._provider.extend(session.sandbox_id)
        except Exception as exc:  # noqa: BLE001 - extend is best-effort
            log_warning(f"[scope-sandbox] extend failed scope={session.scope_id}: {exc}")

    async def touch(self, scope_id: str, user_id: str, *, now: float | None = None) -> None:
        # Bookkeeping only — no provider call (touch is per-activity and hot).
        # The provider timeout is (re)extended on acquire + via a generous
        # create timeout; the reaper stops idle sessions before that anyway.
        async with self._lock(scope_id):
            session = self._store.get(scope_id)
            if session is None:
                return
            self._touch(session, user_id, self._now(now))
            self._store.put(session)

    async def release(self, scope_id: str, user_id: str, *, now: float | None = None) -> None:
        """Drop a user's live connection. Does NOT stop the sandbox — the reaper
        reclaims it once the policy says so (others may still be using it)."""
        async with self._lock(scope_id):
            session = self._store.get(scope_id)
            if session is None:
                return
            session.connected_users.discard(user_id)
            session.last_active_at = self._now(now)
            self._store.put(session)

    def record_pull_cost(self, scope_id: str, seconds: float, *, repo_size_bytes: int = 0) -> None:
        """Caller reports the measured full-pull time after a CREATED acquire, so
        the policy can keep pricier-to-rebuild scopes warm longer."""
        session = self._store.get(scope_id)
        if session is None:
            return
        session.last_full_pull_seconds = seconds
        if repo_size_bytes:
            session.repo_size_bytes = repo_size_bytes
        self._store.put(session)

    # ── governance ────────────────────────────────────────────────────

    async def revoke_user(self, scope_id: str, user_id: str) -> int:
        """Offboarding: revoke a user's SSH access (via revoke_hook) and drop
        them from the shared sandbox's tracking. Returns the number of users
        still connected. The sandbox keeps running for the others; the reaper
        reclaims it once empty."""
        async with self._lock(scope_id):
            session = self._store.get(scope_id)
            if session is None:
                return 0
            if self._revoke_hook is not None:
                try:
                    await self._revoke_hook(self._provider, session.sandbox_id, user_id)
                except Exception as exc:  # noqa: BLE001 - revoke is best-effort; still drop tracking
                    log_warning(f"[scope-sandbox] ssh revoke failed scope={scope_id} user={user_id}: {exc}")
            session.connected_users.discard(user_id)
            session.recent_user_events.pop(user_id, None)
            self._store.put(session)
            return len(session.connected_users)

    async def kill_scope(self, scope_id: str) -> bool:
        """Admin force-reclaim of a scope's sandbox."""
        async with self._lock(scope_id):
            session = self._store.get(scope_id)
            if session is None:
                return False
            try:
                await self._provider.destroy(session.sandbox_id)
            except Exception as exc:  # noqa: BLE001 - still drop the record
                log_warning(f"[scope-sandbox] kill_scope destroy failed scope={scope_id}: {exc}")
            self._store.delete(scope_id)
            return True

    # ── reaper ────────────────────────────────────────────────────────

    async def reap(self, *, now: float | None = None, only_provider: str | None = None) -> ReapSummary:
        """Apply the reclamation policy across sessions. ``only_provider`` limits
        the sweep to sessions created by this provider — required when the shared
        store holds mixed-provider sessions (this manager can only stop/destroy
        its own provider's sandboxes)."""
        now = self._now(now)
        summary = ReapSummary()
        for snapshot in self._store.list_all():
            if only_provider is not None and snapshot.provider != only_provider:
                continue
            scope_id = snapshot.scope_id
            async with self._lock(scope_id):
                session = self._store.get(scope_id)
                if session is None:
                    continue
                decision = decide(session, now, self._config)
                try:
                    if decision is SessionDecision.STOP:
                        await self._apply_stop(session, now)
                        summary.stopped += 1
                    elif decision is SessionDecision.DESTROY:
                        await self._provider.destroy(session.sandbox_id)
                        self._store.delete(scope_id)
                        summary.destroyed += 1
                    else:
                        summary.kept += 1
                except Exception as exc:  # noqa: BLE001 - one failure must not stop the sweep
                    summary.errors += 1
                    log_warning(f"[scope-sandbox] reap {decision} failed scope={scope_id}: {exc}")
        if summary.stopped or summary.destroyed or summary.errors:
            log_info(
                f"[scope-sandbox] reap kept={summary.kept} stopped={summary.stopped} "
                f"destroyed={summary.destroyed} errors={summary.errors}"
            )
        return summary

    async def _apply_stop(self, session: SandboxSession, now: float) -> None:
        """STOP keeps the disk on providers that support it; otherwise the only
        way to stop billing is to destroy (next use pays a full pull)."""
        if self._provider.capabilities().supports_stop_resume:
            await self._provider.stop(session.sandbox_id)
            session.state = SandboxState.STOPPED
            session.last_state_change_at = now
            self._store.put(session)
        else:
            await self._provider.destroy(session.sandbox_id)
            self._store.delete(session.scope_id)

    # ── capacity-pressure eviction ────────────────────────────────────

    async def evict_to_capacity(self, max_active: int, *, now: float | None = None) -> list[str]:
        """Stop the lowest-value idle sessions when the number of RUNNING
        sandboxes exceeds ``max_active``. Returns the evicted scope ids."""
        now = self._now(now)
        running = [
            s for s in self._store.list_all()
            if s.state in (SandboxState.RUNNING, SandboxState.PENDING)
        ]
        excess = len(running) - max_active
        if excess <= 0:
            return []
        victims = select_for_eviction(running, now, self._config, excess)
        evicted: list[str] = []
        for victim in victims:
            async with self._lock(victim.scope_id):
                session = self._store.get(victim.scope_id)
                if session is None or session.connected_users:
                    continue
                try:
                    await self._apply_stop(session, now)
                    evicted.append(victim.scope_id)
                except Exception as exc:  # noqa: BLE001
                    log_warning(f"[scope-sandbox] evict failed scope={victim.scope_id}: {exc}")
        return evicted
