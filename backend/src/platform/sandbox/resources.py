"""Destroy and verify provider resources known only by provider and id.

Project deletion and legacy Scope Sandbox retirement hold a provider name and a
provider resource id, not a live channel. ``pool`` names the provider account
that owns the resource (see ``port.RESOURCE_POOLS``). Both operations are
idempotent; an unknown provider or pool fails closed.
"""

from __future__ import annotations

from .port import RESOURCE_POOLS
from .registry import runtime_provider


def _checked_pool(pool: str) -> str:
    if pool not in RESOURCE_POOLS:
        raise ValueError("Unknown sandbox resource pool")
    return pool


async def destroy_resource(provider: str, resource_id: str, *, pool: str = "runtime") -> None:
    pool = _checked_pool(pool)
    runtime = runtime_provider(provider)
    try:
        await runtime.destroy_resource(resource_id, pool=pool)
    finally:
        await runtime.aclose()


async def resource_absent(provider: str, resource_id: str, *, pool: str = "runtime") -> bool:
    pool = _checked_pool(pool)
    runtime = runtime_provider(provider)
    try:
        return await runtime.resource_absent(resource_id, pool=pool)
    finally:
        await runtime.aclose()
