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
retires machines left by the removed Scope Sandbox product.
"""

from .errors import WorkerDisconnected, WorkerLost, WorkspaceUnavailable
from .port import (
    RESOURCE_POOLS,
    SEND_TIMEOUT,
    RuntimeCapabilities,
    RuntimeConnection,
    RuntimeProvider,
    RuntimeTransition,
    resource_identity,
)
from .reaper import run_execution_reaper_once, start_sandbox_reaper
from .registry import PROVIDERS, close_provider_transports, default_provider, runtime_provider
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
    "WorkspaceUnavailable",
    "close_provider_transports",
    "default_provider",
    "destroy_resource",
    "durable_execution_store",
    "resource_absent",
    "resource_identity",
    "run_execution_reaper_once",
    "runtime_provider",
    "start_sandbox_reaper",
    "workspace_event",
    "workspace_stage",
]
