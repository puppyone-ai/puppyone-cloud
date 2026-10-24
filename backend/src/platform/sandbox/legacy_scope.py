"""Retire machines left behind by the removed Scope Sandbox product.

Scope Sandboxes can no longer be created. A ``scope_sandbox_sessions`` row that
is not yet ``destroyed`` may still own a provider machine, so every sandbox
reaper pass destroys it in the ``legacy_scope`` pool, verifies its absence and
then marks the row destroyed. Project deletion still reads these rows, so they
stay until the table's Contract migration; delete this module together with it.
"""

from __future__ import annotations

import time
from typing import Protocol

from src.infra.execution.runner import run_blocking
from src.utils.logger import log_warning

from .resources import destroy_resource, resource_absent

TABLE = "scope_sandbox_sessions"


class LegacyScopeSessions(Protocol):
    def live(self) -> list[dict]: ...
    def mark_destroyed(self, scope_id: str, now: float) -> None: ...


class SupabaseLegacyScopeSessions:
    def __init__(self, client=None) -> None:
        if client is None:
            from src.infra.supabase.client import SupabaseClient

            client = SupabaseClient().client
        self._client = client

    def live(self) -> list[dict]:
        response = (
            self._client.table(TABLE)
            .select("scope_id,provider,sandbox_id")
            .neq("state", "destroyed")
            .execute()
        )
        return list(response.data or [])

    def mark_destroyed(self, scope_id: str, now: float) -> None:
        self._client.table(TABLE).update(
            {"state": "destroyed", "last_state_change_at": now}
        ).eq("scope_id", scope_id).neq("state", "destroyed").execute()


async def retire_legacy_scope_sandboxes_once(
    sessions: LegacyScopeSessions | None = None,
    *,
    now: float | None = None,
) -> int:
    sessions = sessions or SupabaseLegacyScopeSessions()
    retired = 0
    for row in await run_blocking(sessions.live):
        provider, sandbox_id = str(row["provider"]), str(row["sandbox_id"])
        try:
            await destroy_resource(provider, sandbox_id, pool="legacy_scope")
            if not await resource_absent(provider, sandbox_id, pool="legacy_scope"):
                continue
        except Exception as exc:
            # Keep the row live: the next pass retries, and Project deletion
            # still sees it. Operators see the failure class in the logs.
            log_warning(
                f"[LegacyScopeSandbox] retirement of a {provider} machine failed; "
                f"will retry: {type(exc).__name__}"
            )
            continue
        await run_blocking(
            sessions.mark_destroyed, str(row["scope_id"]), time.time() if now is None else now
        )
        retired += 1
    return retired
