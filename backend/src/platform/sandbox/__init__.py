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
"""

from .errors import WorkerDisconnected, WorkerLost, WorkspaceUnavailable
from .port import (
    SEND_TIMEOUT,
    RuntimeCapabilities,
    RuntimeConnection,
    RuntimeProvider,
    RuntimeTransition,
    resource_identity,
)
from .registry import PROVIDERS, close_provider_transports, default_provider, runtime_provider
from .timing import workspace_event, workspace_stage

__all__ = [
    "PROVIDERS",
    "SEND_TIMEOUT",
    "RuntimeCapabilities",
    "RuntimeConnection",
    "RuntimeProvider",
    "RuntimeTransition",
    "WorkerDisconnected",
    "WorkerLost",
    "WorkspaceUnavailable",
    "close_provider_transports",
    "default_provider",
    "resource_identity",
    "runtime_provider",
    "workspace_event",
    "workspace_stage",
]
