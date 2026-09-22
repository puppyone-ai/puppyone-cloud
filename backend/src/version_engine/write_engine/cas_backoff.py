"""Shared CAS-retry backoff for the write engine (ISSUE-013).

Losing writers on a hot scope/project root previously retried the root CAS
immediately with no delay, forming a thundering herd where every wasted attempt
re-reads + re-merges. Full-jitter exponential backoff spreads contenders out and
sheds load under concurrency. Used by operation_writer / submission_writer /
rollback_writer, which all share the same fixed-attempt CAS loop.
"""

from __future__ import annotations

import asyncio
import random

_CAS_BACKOFF_BASE_MS = 25
_CAS_BACKOFF_MAX_MS = 500


async def cas_backoff(attempt: int) -> None:
    """Sleep before a CAS retry using full-jitter exponential backoff.

    ``attempt`` is 0-based; the first attempt (0) never sleeps.
    """
    if attempt <= 0:
        return
    ceiling_ms = min(_CAS_BACKOFF_BASE_MS * (2 ** (attempt - 1)), _CAS_BACKOFF_MAX_MS)
    await asyncio.sleep(random.uniform(0, ceiling_ms) / 1000.0)
