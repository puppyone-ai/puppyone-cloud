"""A Workspace's own Fly app, and the private network of the same name.

Fly never forwards packets between private networks, so the boundary between
machines holds even for root inside one of them. The name comes from the
Workspace id: a lost create reply is recovered by name, and a rebuild returns
to the same network, because Fly never deletes a network name.

There is no shared network to fall back to. A resource that is not on its own
network is refused, never served.
"""

import base64
from uuid import UUID

from src.platform.sandbox.errors import WorkspaceUnavailable

NOT_ISOLATED = "workbench_network_not_isolated"


def workspace_app(workbench_id: str) -> str:
    return "pw-" + base64.b32encode(UUID(workbench_id).bytes).decode().rstrip("=").lower()


def dedicated(resource: dict) -> bool:
    """True only for a name this Workspace owns, so nothing else is ever deleted."""
    workbench_id = resource.get("workbench_id")
    return bool(workbench_id) and resource.get("app") == workspace_app(workbench_id)


def require_dedicated(resource: dict) -> None:
    if not dedicated(resource):
        raise WorkspaceUnavailable(NOT_ISOLATED)
