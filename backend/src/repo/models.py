"""Domain models for the repo redesign — pure dataclasses, no DB knowledge.

Repository implementations (scope_repository, connector_repository, …) own
the row ↔ model translation. Routers/services consume these models without
caring whether they came from Supabase, an in-memory store, or a test stub.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.platform.repository_target.models import RepositoryTarget, repository_target_scope_id

IMPORT_ONLY_CONNECTOR_PROVIDERS = frozenset({"github"})
DEPRECATED_ACCESS_CONNECTOR_PROVIDERS = frozenset({"filesystem"})


# ──────────────────────────────────────────────────────────────────────────
# Scope
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class RepositoryScope:
    """A real, non-empty path boundary within a Project repository."""

    id: str
    project_id: str
    name: str
    path: str                       # canonical, non-empty, no leading/trailing /
    exclude: list[str]
    max_mode: str                   # 'r' | 'rw'
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ResolvedAccessSurfaceCredential:
    """An authenticated CLI Access Surface before Scope geometry is loaded."""

    credential_id: str
    credential_type: str
    access_surface_id: str
    project_id: str
    scope_id: str
    mode_ceiling: str


@dataclass(frozen=True, slots=True)
class ResolvedScopeCredential:
    """A machine credential and its exact, capability-clamped Scope target."""

    credential_id: str
    credential_type: str
    access_surface_id: str
    scope: RepositoryScope

    @property
    def project_id(self) -> str:
        return self.scope.project_id

    @property
    def scope_id(self) -> str:
        return self.scope.id


# ──────────────────────────────────────────────────────────────────────────
# Connector
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class Connector:
    """Compatibility DTO for Project-root or Scope-bound Access surfaces."""

    id: str
    target: RepositoryTarget
    provider: str                   # 'cli', 'agent', 'notion', 'gmail', ...
    name: str
    direction: str                  # 'bidirectional' | 'inbound' | 'outbound'
    config: dict[str, Any]          # provider-specific
    policy: dict[str, Any]          # connector-specific permission policy
    oauth_connection_id: Optional[int]   # FK → oauth_connections.id (BIGINT)
    trigger: dict[str, Any]         # {"type": "manual" | "scheduled" | "on_change", ...}
    status: str                     # 'active' | 'paused' | 'syncing' | 'error'
    last_run_at: Optional[datetime]
    last_run_id: Optional[str]
    error_message: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime

    @property
    def project_id(self) -> str:
        return self.target.project_id

    @property
    def scope_id(self) -> Optional[str]:
        return repository_target_scope_id(self.target)

    @property
    def is_builtin(self) -> bool:
        # Standard Access surfaces have dedicated lifecycle operations and
        # cannot be deleted or manually run through the connector facade.
        return self.provider in ("git_remote", "cli", "agent")

    @property
    def is_oauth_backed(self) -> bool:
        # Self-auth providers (raw URL, REST API with API key in config) carry
        # NULL oauth_connection_id; OAuth-backed providers carry a non-NULL one.
        return self.oauth_connection_id is not None

    @property
    def is_access_surface(self) -> bool:
        """Whether this row represents an ongoing Access method.

        Legacy datasource rows used connector-shaped DTOs for one-shot imports.
        Those rows are still useful history/debug state, but they are not
        "ways into" a scope and should not be returned by Access endpoints by
        default.
        """
        if self.provider in IMPORT_ONLY_CONNECTOR_PROVIDERS:
            return False
        if self.provider in DEPRECATED_ACCESS_CONNECTOR_PROVIDERS:
            return False
        return (self.trigger or {}).get("type") != "import_once"
