"""Explicit, bounded authorization reuse for a running domain operation."""

import asyncio
import time

from src.infra.execution.runner import run_owned


class OperationAuthorization:
    """Reuse one decision; recheck current facts before work after 30 seconds.

    This is task-local, never a process cache or a substitute for transactional
    publication checks. A slow external call is joined/cancelled by its owner;
    the next protected step must call current() before it can proceed.
    """

    def __init__(self, authorization, project_id, user_id, action, *, clock=time.monotonic):
        self._authorization = authorization
        self._project_id, self._user_id, self._action = project_id, user_id, action
        self._clock, self._expires = clock, 0.0
        self._grant = None
        self._lock = asyncio.Lock()

    async def current(self):
        async with self._lock:
            if self._grant is None or self._clock() >= self._expires:
                self._grant = await run_owned(
                    self._authorization.authorize_fresh,
                    self._project_id, self._user_id, self._action,
                )
                self._expires = self._clock() + 30.0
            return self._grant
