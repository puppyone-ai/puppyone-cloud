"""Whether this process's provider configuration can operate a persisted binding.

A binding (provider + artifact) is pinned when compute is allocated. Another
process may hold different provider configuration, so before acting on an
existing binding it proves the provider is registered and configured here.
This is a local configuration check; it performs no provider I/O.
"""

from __future__ import annotations

from collections.abc import Mapping

from .errors import WorkspaceUnavailable
from .registry import PROVIDERS, runtime_provider, workspace_providers

NOT_CONFIGURED = "workbench_provider_not_configured"


async def require_binding(
    binding: Mapping, resource: Mapping | None = None, *, workspace_only: bool = False
) -> None:
    """Raise WorkspaceUnavailable(NOT_CONFIGURED) unless this process can serve it.

    ``workspace_only`` requires a provider served by this workspace deployment,
    including explicitly retained providers for pre-existing bindings.
    """
    name = binding.get("provider")
    try:
        selected = workspace_providers() if workspace_only else ()
    except ValueError as exc:
        raise WorkspaceUnavailable(NOT_CONFIGURED) from exc
    if name not in PROVIDERS or (workspace_only and name not in selected):
        raise WorkspaceUnavailable(NOT_CONFIGURED)
    provider = runtime_provider(name)
    try:
        provider.validate(binding.get("artifact"), dict(resource) if resource else None)
    except ValueError as exc:
        raise WorkspaceUnavailable(NOT_CONFIGURED) from exc
    finally:
        await provider.aclose()
