"""Sandbox: pluggable compute providers behind one RuntimeProvider interface.

Layout rules (enforced by tests/architecture/test_entrypoint_architecture.py):

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
``catalog`` registers sandbox Profiles (machine shape) and Features (optional
capabilities); Providers receive resolved values and never import it.
"""

from .bindings import require_binding
from .catalog import (
    FEATURES,
    PROFILES,
    SETTING_KEYS,
    SandboxFeature,
    SandboxProfile,
    binding_profile,
    default_profile,
    enabled_features,
    profile_artifact,
    resolve_features,
    settings_profile,
    validate_settings,
)
from .errors import WorkerDisconnected, WorkerLost, WorkspaceDiskLost, WorkspaceUnavailable
from .port import (
    RESOURCE_POOLS,
    SEND_TIMEOUT,
    STREAM_COMMANDS,
    RuntimeCapabilities,
    RuntimeConnection,
    RuntimeProvider,
    RuntimeStream,
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
    "FEATURES",
    "PROFILES",
    "PROVIDERS",
    "RESOURCE_POOLS",
    "SEND_TIMEOUT",
    "STREAM_COMMANDS",
    "ExecutionSession",
    "ExecutionSessionStore",
    "RuntimeCapabilities",
    "RuntimeConnection",
    "RuntimeProvider",
    "RuntimeStream",
    "RuntimeTransition",
    "SETTING_KEYS",
    "SandboxFeature",
    "SandboxProfile",
    "WorkerDisconnected",
    "WorkerLost",
    "WorkspaceDiskLost",
    "WorkspaceUnavailable",
    "binding_profile",
    "close_provider_transports",
    "default_profile",
    "default_provider",
    "destroy_resource",
    "durable_execution_store",
    "enabled_features",
    "profile_artifact",
    "reclamation_passes",
    "require_binding",
    "resolve_features",
    "settings_profile",
    "validate_settings",
    "resource_absent",
    "resource_identity",
    "run_execution_reaper_once",
    "runtime_provider",
    "workspace_event",
    "workspace_providers",
    "workspace_stage",
]
