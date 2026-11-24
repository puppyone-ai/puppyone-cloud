"""A Workspace's own Fly app, and the private network of the same name.

Fly never forwards packets between private networks, so the boundary between
machines holds even for root inside one of them. The name comes from the
Workspace id: a lost create reply is recovered by name, and a rebuild returns
to the same network, because Fly never deletes a network name.

In Tier 2 (``fly_host_export``), isolated Agent VMs share their Project's
default Workspace app & 6pn private network (``host_workbench_id``) so cross-
Project isolation remains hardware-enforced while Agent VMs never own or
delete the Project app itself.
"""

import base64
from uuid import UUID


def workspace_app(workbench_id: str) -> str:
    return "pw-" + base64.b32encode(UUID(workbench_id).bytes).decode().rstrip("=").lower()


def dedicated(resource: dict) -> bool:
    """True only for a name this Workspace owns, so nothing else is ever deleted."""
    workbench_id = resource.get("workbench_id")
    return bool(workbench_id) and resource.get("app") == workspace_app(workbench_id)


def shared_dedicated_network(resource: dict) -> bool:
    """True when an Agent VM is placed inside its Project Host Workspace's dedicated Fly app."""
    host_workbench_id = resource.get("host_workbench_id")
    return bool(host_workbench_id) and resource.get("app") == workspace_app(host_workbench_id)


def isolated_app(resource: dict) -> bool:
    """True for any Workspace bound to a dedicated per-Workspace or per-Project Fly app."""
    return dedicated(resource) or shared_dedicated_network(resource)
