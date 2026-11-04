"""A Workspace's own Fly app, and the private network of the same name.

Fly never forwards packets between private networks, so the boundary between
machines holds even for root inside one of them. The name comes from the
Workspace id: a lost create reply is recovered by name, and a rebuild returns
to the same network, because Fly never deletes a network name.
"""

import base64
from uuid import UUID


def workspace_app(workbench_id: str) -> str:
    return "pw-" + base64.b32encode(UUID(workbench_id).bytes).decode().rstrip("=").lower()


def dedicated(resource: dict) -> bool:
    """True only for a name this Workspace owns, so nothing else is ever deleted."""
    workbench_id = resource.get("workbench_id")
    return bool(workbench_id) and resource.get("app") == workspace_app(workbench_id)
