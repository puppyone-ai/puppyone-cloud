"""Whether this process's provider configuration can operate a persisted binding.

A binding (provider + artifact) is pinned when compute is allocated. Another
process may hold different provider configuration, so before acting on an
existing binding it proves the provider is registered and configured here.
This is a local configuration check; it performs no provider I/O.
"""

from __future__ import annotations

from collections.abc import Mapping

from .errors import WorkspaceUnavailable
from .registry import PROVIDERS, default_provider, runtime_provider

NOT_CONFIGURED = "workbench_provider_not_configured"


async def require_binding(
    binding: Mapping, resource: Mapping | None = None, *, default_only: bool = False
) -> None:
    """Raise WorkspaceUnavailable(NOT_CONFIGURED) unless this process can serve it.

    ``default_only`` requires the deployment's default provider, for owners that
    operate only that provider (workspace lifecycle).
    """
    name = binding.get("provider")
    try:
        selected = default_provider() if default_only else None
    except ValueError as exc:
        raise WorkspaceUnavailable(NOT_CONFIGURED) from exc
    if name not in PROVIDERS or (default_only and name != selected):
        raise WorkspaceUnavailable(NOT_CONFIGURED)
    provider = runtime_provider(name)
    try:
        provider.validate(binding.get("artifact"), dict(resource) if resource else None)
    except ValueError as exc:
        raise WorkspaceUnavailable(NOT_CONFIGURED) from exc
    finally:
        await provider.aclose()
