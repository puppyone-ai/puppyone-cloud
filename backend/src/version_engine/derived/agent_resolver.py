"""Agent-assisted merge dispatch — L6 plumbing for ``agent_*`` policies.

Conflict policies ``agent_review`` and ``agent_auto_resolve`` make the
engine queue the conflict (same shape as ``manual_review``) but mark
the pending row's ``resolver_kind = "agent"``. This module is the
bridge from "row landed in version_conflicts" to "an agent runner takes
the work".

Architecture:

  L5 engine
    ↓ pending_conflict_created outbox event
  L6 outbox.process_version_outbox_batch
    ↓ _handle_pending_conflict
  _pending_conflict_hook  ← agent runners register here
    ↓
  AgentResolverDispatcher.dispatch(pending_row)
    ↓ (delegates to a concrete runner)
  HttpAgentRunner / InProcessAgentRunner / NoopAgentRunner

Runners decide synchronously whether to:
  - accept(resolution_tree_id or resolution_files): re-enter the engine
    via ``engine.resolve(ConflictResolutionIntent)`` → commit lands
  - reject(reason): close the row with no commit
  - defer(): leave the row as-is so a human can pick it up later

The runner protocol intentionally mirrors what a human reviewer would
do via ``POST /conflicts/{id}/resolve`` so the same ``engine.resolve``
path covers both. Agents don't get a special privilege; they just
make the decision faster.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from src.utils.logger import log_error, log_info, log_warning


# ── Runner protocol ─────────────────────────────────────────────


@dataclass(frozen=True)
class AgentDecision:
    """What an agent runner decided about a pending conflict.

    Exactly one of ``resolution_tree_id`` / ``resolution_files`` is
    populated on ``decision == 'accept'``. ``defer`` keeps the row
    in its current state so a human can pick up the work; useful
    when the agent gives up partway through a multi-file merge.
    """

    decision: str  # "accept" | "reject" | "defer"
    resolution_tree_id: str = ""
    resolution_files: dict[str, bytes] | None = None
    resolution_message: str = ""
    resolver_actor: str = ""


class AgentRunner(Protocol):
    """One implementation per agent backend (Claude, internal model, …)."""

    async def resolve(self, pending_row: dict[str, Any]) -> AgentDecision:
        ...


class NoopAgentRunner:
    """Default runner — logs and defers every pending row.

    Used when no agent backend is registered. Keeps the L6 dispatch
    surface live so an operator sees the row even before agents come
    online.
    """

    async def resolve(self, pending_row: dict[str, Any]) -> AgentDecision:
        log_info(
            "[agent-resolver][noop] pending_conflict "
            f"id={pending_row.get('pending_conflict_id')} "
            f"policy={pending_row.get('policy')} — deferring "
            f"(no agent runner registered)",
        )
        return AgentDecision(
            decision="defer",
            resolver_actor="agent:noop",
            resolution_message=(
                "no agent backend configured; row left in pending for "
                "human review"
            ),
        )


# ── Dispatcher ──────────────────────────────────────────────────


class AgentResolverDispatcher:
    """Single entry point for all agent-kind pending conflicts.

    A process registers exactly one ``AgentRunner`` at startup
    (typically inside ``main.py``'s lifespan, or a worker bootstrap).
    The outbox calls ``dispatch()`` with the full pending row whenever
    ``resolver_kind == "agent"``.
    """

    _instance: "AgentResolverDispatcher | None" = None

    def __init__(self, runner: AgentRunner):
        self._runner = runner

    @classmethod
    def get(cls) -> "AgentResolverDispatcher | None":
        return cls._instance

    @classmethod
    def install(cls, runner: AgentRunner) -> "AgentResolverDispatcher":
        cls._instance = cls(runner)
        log_info(
            f"[agent-resolver] installed runner={type(runner).__name__}",
        )
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Test-only: clear the installed runner."""
        cls._instance = None

    async def dispatch(self, pending_row: dict[str, Any]) -> None:
        """Run the agent and, if it picked a side, route the decision
        back through ``engine.resolve`` so the result lands the same
        way a human resolution does.
        """
        project_id = pending_row.get("project_id", "")
        pending_id = pending_row.get("pending_conflict_id", "")
        scope_path = (pending_row.get("scope_path") or "")

        try:
            decision = await self._runner.resolve(pending_row)
        except Exception as exc:
            log_error(
                f"[agent-resolver] runner raised for pending={pending_id}: "
                f"{exc}; leaving row as pending for human takeover",
            )
            return

        if decision.decision == "defer":
            log_info(
                f"[agent-resolver] runner deferred pending={pending_id}; "
                f"row stays in pending",
            )
            return

        # Re-enter the engine via the same path a human reviewer uses.
        # Local import keeps the resolver module decoupled from the
        # engine class graph at import time.
        from src.version_engine.write_engine.engine import VersionWriteEngine
        from src.version_engine.domain.intents import ConflictResolutionIntent
        from src.version_engine.bootstrap.dependencies import (
            build_worker_version_engine_container,
        )

        container = build_worker_version_engine_container()
        engine = container.write_engine()

        intent = ConflictResolutionIntent(
            project_id=project_id,
            pending_conflict_id=pending_id,
            scope_path=scope_path,
            resolver_actor=decision.resolver_actor or "agent:auto",
            source_channel="access_sandbox",
            resolution_tree_id=decision.resolution_tree_id,
            resolution_files=decision.resolution_files,
            resolution_message=(
                decision.resolution_message or "resolved by agent"
            ),
            decision=decision.decision,  # type: ignore[arg-type]
        )
        try:
            result = await engine.resolve(intent)
        except Exception as exc:
            log_error(
                f"[agent-resolver] engine.resolve failed for "
                f"pending={pending_id}: {exc}; row may stay in resolving",
            )
            return

        log_info(
            f"[agent-resolver] pending={pending_id} status={result.status} "
            f"commit_id={result.commit_id[:12] if result.commit_id else ''}",
        )


# ── Outbox-hook bridge ──────────────────────────────────────────


def _ensure_loop_run(coro: Awaitable) -> None:
    """Run an awaitable from a sync context.

    The outbox worker is a sync ARQ task; we need to run the agent
    runner (which is async because most LLM SDKs are async). This
    helper handles both cases: an existing loop (rare here) and the
    common "no loop" case via ``asyncio.run``.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


def agent_resolver_outbox_hook(row: dict[str, Any]) -> None:
    """``register_pending_conflict_hook``-compatible callable.

    Routes the row to the agent dispatcher when the pending row has
    ``resolver_kind == "agent"``. Other resolver kinds fall through so
    the outbox can keep logging them as "no resolver hook registered".
    """
    payload = row.get("payload") or {}
    resolver_kind = (
        payload.get("resolver_kind")
        or _peek_resolver_kind(row.get("project_id", ""), payload.get("pending_conflict_id", ""))
    )
    if resolver_kind != "agent":
        return

    dispatcher = AgentResolverDispatcher.get()
    if dispatcher is None:
        log_warning(
            f"[agent-resolver] agent-kind pending row landed but no "
            f"AgentResolverDispatcher installed; row id="
            f"{payload.get('pending_conflict_id')!r} stays pending",
        )
        return

    _ensure_loop_run(dispatcher.dispatch(row))


def _peek_resolver_kind(project_id: str, pending_conflict_id: str) -> str:
    """Best-effort lookup of ``resolver_kind`` from the conflicts table.

    The outbox payload usually carries ``resolver_kind`` directly (it
    was written there when the row was queued), but older rows or
    rebuilt outbox events may not — fall back to a Supabase read.
    """
    if not project_id or not pending_conflict_id:
        return ""
    try:
        from src.infra.supabase.client import SupabaseClient
        from src.version_engine.infrastructure.supabase.db_names import (
            CONFLICTS_TABLE,
        )
        client = SupabaseClient().client
        resp = (
            client.table(CONFLICTS_TABLE)
            .select("resolver_kind")
            .eq("project_id", project_id)
            .eq("pending_conflict_id", pending_conflict_id)
            .maybe_single()
            .execute()
        )
        row = getattr(resp, "data", None) or {}
        return str(row.get("resolver_kind") or "")
    except Exception:
        return ""


# ── Convenience: function-based runner adapter ──────────────────


def runner_from_callable(
    fn: Callable[[dict[str, Any]], Awaitable[AgentDecision]],
) -> AgentRunner:
    """Adapt a plain async function into the ``AgentRunner`` protocol.

    Useful in tests + small one-shot integrations::

        async def my_agent(row):
            return AgentDecision(
                decision="accept",
                resolution_files={"a.md": b"merged"},
                resolver_actor="agent:test",
            )

        AgentResolverDispatcher.install(runner_from_callable(my_agent))
    """

    class _CallableRunner:
        async def resolve(self, pending_row: dict[str, Any]) -> AgentDecision:
            return await fn(pending_row)

    return _CallableRunner()


__all__ = [
    "AgentDecision",
    "AgentRunner",
    "AgentResolverDispatcher",
    "NoopAgentRunner",
    "agent_resolver_outbox_hook",
    "runner_from_callable",
]


# Helper to base64-encode resolution_files for agents that want to
# return bytes directly. Mirrors the frontend's encoder so agent code
# and human-resolver code share the wire encoding.
def encode_files_for_resolution(files: dict[str, bytes]) -> dict[str, str]:
    return {path: base64.b64encode(data).decode("ascii") for path, data in files.items()}
