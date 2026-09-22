"""Provider selection — the "two versions, user-selectable" entry point.

``build_provider`` is a pure constructor (no global state) so it's unit-testable
and lets callers inject pre-built clients. ``provider_from_settings`` reads the
app config to pick the default for a deployment; an enterprise/project can
override the name to choose Fly (managed) vs E2B (self-hostable/compliance).
"""

from __future__ import annotations

from src.platform.scope_sandbox.e2b_provider import E2BClient, E2BProvider, SdkE2BClient
from src.platform.scope_sandbox.fly_provider import FlyMachinesProvider
from src.platform.scope_sandbox.provider import SandboxProvider
from src.platform.scope_sandbox.registry import (
    InMemorySandboxSessionStore,
    SandboxSessionStore,
)

PROVIDER_FLY = "fly"
PROVIDER_E2B = "e2b"
SUPPORTED_PROVIDERS = (PROVIDER_FLY, PROVIDER_E2B)

STORE_MEMORY = "memory"
STORE_SUPABASE = "supabase"
SUPPORTED_STORES = (STORE_MEMORY, STORE_SUPABASE)


def build_provider(
    name: str,
    *,
    fly_app: str = "",
    fly_token: str = "",
    fly_image: str = "",
    e2b_api_key: str | None = None,
    e2b_template: str = "",
    e2b_client: E2BClient | None = None,
) -> SandboxProvider:
    """Build a sandbox provider by name. Injectables (``e2b_client``) keep this
    testable without live SDKs."""
    if name == PROVIDER_FLY:
        if not (fly_app and fly_token):
            raise ValueError("Fly provider requires fly_app and fly_token")
        return FlyMachinesProvider(fly_app, fly_token, default_image=fly_image)
    if name == PROVIDER_E2B:
        return E2BProvider(
            e2b_client or SdkE2BClient(api_key=e2b_api_key, template=e2b_template)
        )
    raise ValueError(f"unknown scope-sandbox provider {name!r}; expected one of {SUPPORTED_PROVIDERS}")


def provider_from_settings(settings, name: str | None = None) -> SandboxProvider:
    """Build the provider chosen by config (or an explicit ``name`` override)."""
    chosen = name or getattr(settings, "SCOPE_SANDBOX_PROVIDER", PROVIDER_FLY)
    return build_provider(
        chosen,
        fly_app=getattr(settings, "SCOPE_SANDBOX_FLY_APP", ""),
        fly_token=getattr(settings, "SCOPE_SANDBOX_FLY_TOKEN", ""),
        fly_image=getattr(settings, "SCOPE_SANDBOX_FLY_IMAGE", ""),
        e2b_api_key=getattr(settings, "E2B_API_KEY", None),
        e2b_template=getattr(settings, "SCOPE_SANDBOX_E2B_TEMPLATE", ""),
    )


def build_session_store(name: str) -> SandboxSessionStore:
    """Build a store. Production defaults to durable ``supabase``;
    ``memory`` exists only when tests/local development select it explicitly."""
    if name == STORE_MEMORY:
        return InMemorySandboxSessionStore()
    if name == STORE_SUPABASE:
        from src.platform.scope_sandbox.supabase_store import SupabaseSandboxSessionStore
        return SupabaseSandboxSessionStore()
    raise ValueError(f"unknown scope-sandbox store {name!r}; expected one of {SUPPORTED_STORES}")


def store_from_settings(settings) -> SandboxSessionStore:
    return build_session_store(getattr(settings, "SCOPE_SANDBOX_STORE", STORE_SUPABASE))
