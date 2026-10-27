"""Sandbox: pluggable compute providers behind one RuntimeProvider interface.

Layout rules (enforced by tests/platform/test_entrypoint_architecture.py):

- Callers outside this package import only from ``src.platform.sandbox``,
  never from ``src.platform.sandbox.providers.*``.
- Each provider lives in ``providers/<name>/``, implements ``RuntimeProvider``
  (``port.py``) and depends only on ``port``, ``errors``, ``timing`` and its own
  SDK. Providers never import each other.
- This package never imports Workspace, Access or Agent code.
- Adding a provider means a new ``providers/<name>/`` folder plus one
  ``registry.PROVIDERS`` entry.

A provider owns compute lifecycle and channel transport; it never owns a Run,
authorizes a Project, selects Git refs, or interprets Loop messages.

``store`` records unretained ephemeral provider resources and ``reaper`` stops
idle ones through each provider's ``cleanup``. ``resources`` destroys and
verifies a resource known only by id (Project deletion); ``legacy_scope``
retires machines left by the removed Scope Sandbox product. ``bindings``
proves this process's configuration can operate a persisted binding.
"""

from .bindings import require_binding
from .errors import WorkerDisconnected, WorkerLost, WorkspaceDiskLost, WorkspaceUnavailable
from .port import (
    RESOURCE_POOLS,
    SEND_TIMEOUT,
    RuntimeCapabilities,
    RuntimeConnection,
    RuntimeProvider,
    RuntimeTransition,
    resource_identity,
)
from .reaper import reclamation_passes, run_execution_reaper_once
from .registry import (
    PROVIDERS,
    close_provider_transports,
    default_provider,
    runtime_provider,
    workspace_providers,
)
from .resources import destroy_resource, resource_absent
from .store import ExecutionSession, ExecutionSessionStore, durable_execution_store
from .timing import workspace_event, workspace_stage

__all__ = [
    "PROVIDERS",
    "RESOURCE_POOLS",
    "SEND_TIMEOUT",
    "ExecutionSession",
    "ExecutionSessionStore",
    "RuntimeCapabilities",
    "RuntimeConnection",
    "RuntimeProvider",
    "RuntimeTransition",
    "WorkerDisconnected",
    "WorkerLost",
    "WorkspaceDiskLost",
    "WorkspaceUnavailable",
    "close_provider_transports",
    "default_provider",
    "destroy_resource",
    "durable_execution_store",
    "reclamation_passes",
    "require_binding",
    "resource_absent",
    "resource_identity",
    "run_execution_reaper_once",
    "runtime_provider",
    "workspace_event",
    "workspace_providers",
    "workspace_stage",
]
