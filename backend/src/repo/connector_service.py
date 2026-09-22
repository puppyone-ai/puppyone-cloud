"""Business logic for connectors.

Responsibilities:
  - Refuse to create built-in Access surfaces via the legacy API.
  - Default `name` from provider if not given.
  - Validate direction against provider capabilities.
  - Coordinate execution (Step 2-Run-Now): hand off to engine.execute().
"""

from __future__ import annotations
from typing import Any, Optional

from src.exceptions import AppException, BusinessException, ErrorCode, NotFoundException
from src.repo.access_surface_repository import AccessSurfaceRepository
from src.repo.connector_repository import ConnectorRepository
from src.repo.models import Connector
from src.repo.scope_repository import RepositoryScopeRepository
from src.platform.repository_target.models import (
    ProjectRootTarget,
    RepositoryTarget,
    ScopeTarget,
    repository_target_scope_id,
)


PROVIDERS_BIDIRECTIONAL = frozenset({"git_remote", "cli", "agent"})
# Built-in Access surfaces are explicitly enabled per repository target and
# cannot be created through the third-party connector endpoint.
PROVIDERS_OAUTH_BACKED = frozenset({
    "notion", "gmail", "google_sheets", "google_docs",
    "google_calendar", "google_drive", "google_search_console",
    "github", "linear", "airtable",
})

# Providers whose rows represent import/integration history, not an ongoing
# access method bound to a scope. GitHub repository import now lives under
# ImportJob / project GitHub integration flows instead of Access connectors.
PROVIDERS_IMPORT_ONLY = frozenset({"github"})


def _provider_default_name(provider: str) -> str:
    return provider.replace("_", " ").title()


def _clear_connector_policy_cache(scope_id: str | None, provider: str) -> None:
    """Best-effort cache invalidation for hot-path admission checks."""
    try:
        from src.version_engine.admission.connector_policy import (
            clear_connector_policy_cache,
        )
        clear_connector_policy_cache(scope_id=scope_id, provider=provider)
    except Exception:
        # Policy cache TTL is short; a failed invalidation should not make
        # connector CRUD fail.
        pass


def _clear_connector_admission_cache(scope_id: str | None, provider: str) -> None:
    """Best-effort cache invalidation for all connector admission gates."""
    _clear_connector_policy_cache(scope_id, provider)
    try:
        from src.version_engine.admission.channel_pause import (
            clear_channel_pause_cache,
        )
        clear_channel_pause_cache(scope_id=scope_id, channel=provider)
    except Exception:
        # Channel pause cache TTL is short; CRUD should not fail if
        # invalidation cannot import during startup/test wiring.
        pass


class ConnectorService:
    def __init__(
        self,
        repository: Optional[ConnectorRepository] = None,
        surface_repository: Optional[AccessSurfaceRepository] = None,
        scope_repository: Optional[RepositoryScopeRepository] = None,
    ):
        self._repo = repository or ConnectorRepository()
        self._surfaces = surface_repository or AccessSurfaceRepository()
        self._scopes = scope_repository or RepositoryScopeRepository()

    # ── Reads ────────────────────────────────────────────────────────────

    def list(
        self,
        project_id: str,
        *,
        scope_id: Optional[str] = None,
        provider: Optional[str] = None,
        direction: Optional[str] = None,
        access_surface_only: bool = True,
    ) -> list[Connector]:
        connectors = self._repo.list_by_project(
            project_id, scope_id=scope_id, provider=provider, direction=direction,
        )
        if access_surface_only:
            connectors = [c for c in connectors if c.is_access_surface]
        return connectors

    def get(self, connector_id: str) -> Optional[Connector]:
        return self._repo.get(connector_id)

    # ── Writes ───────────────────────────────────────────────────────────

    def create(
        self,
        *,
        project_id: str,
        target: RepositoryTarget,
        provider: str,
        direction: str,
        name: Optional[str],
        config: Optional[dict[str, Any]],
        policy: Optional[dict[str, Any]],
        oauth_connection_id: Optional[int],
        trigger: Optional[dict[str, Any]],
        created_by: Optional[str],
    ) -> Connector:
        # Built-in Access surfaces have a dedicated idempotent enable action;
        # this third-party connector endpoint never creates them.
        if provider in PROVIDERS_BIDIRECTIONAL:
            raise BusinessException(
                f"'{provider}' access surfaces are created per repository target. "
                "Edit the existing surface instead of creating a new one."
            )

        if provider in PROVIDERS_IMPORT_ONLY or (trigger or {}).get("type") == "import_once":
            raise BusinessException(
                "One-time imports are not Access connectors. Create an import "
                "job or sync binding instead of a repo connector."
            )

        # Direction validation.
        if direction == "bidirectional":
            raise BusinessException(
                "Only built-in Access surfaces "
                "are bidirectional. Third-party providers must choose "
                "'inbound' (import) or 'outbound' (export)."
            )

        if provider in PROVIDERS_OAUTH_BACKED and not oauth_connection_id:
            raise BusinessException(
                f"Provider '{provider}' requires an oauth_connection_id. "
                "Connect this provider via the /connections page first, "
                "then re-create this connector."
            )

        if target.project_id != project_id:
            raise AppException(
                code=ErrorCode.TARGET_KIND_MISMATCH,
                status_code=422,
                message="Repository target is not in this Project",
            )
        if isinstance(target, ScopeTarget):
            # Without this, an invalid Scope target surfaces as a raw composite
            # FK violation that the global handler turns into a generic 500.
            scope = self._scopes.get(target.scope_id)
            if scope is None or scope.project_id != project_id:
                raise NotFoundException(
                    f"Scope {target.scope_id!r} not found in this Project",
                    code=ErrorCode.SCOPE_NOT_FOUND,
                )
        elif not isinstance(target, ProjectRootTarget):
            raise NotFoundException("Unsupported repository target")

        return self._repo.insert(
            project_id=project_id,
            scope_id=repository_target_scope_id(target),
            provider=provider,
            name=name or _provider_default_name(provider),
            direction=direction,
            config=config or {},
            policy=policy or {},
            oauth_connection_id=oauth_connection_id,
            trigger=trigger or {"type": "manual"},
            created_by=created_by,
        )

    def enable_target_defaults(
        self,
        *,
        project_id: str,
        target: RepositoryTarget,
        created_by: str | None,
    ) -> list[Connector]:
        """Idempotently enable Git and CLI for one exact target."""

        if target.project_id != project_id:
            raise AppException(
                code=ErrorCode.TARGET_KIND_MISMATCH,
                status_code=422,
                message="Repository target is not in this Project",
            )
        scope = None
        if isinstance(target, ScopeTarget):
            scope = self._scopes.get(target.scope_id)
            if scope is None or scope.project_id != project_id:
                raise NotFoundException(
                    "Repository Scope not found in this Project",
                    code=ErrorCode.SCOPE_NOT_FOUND,
                )
        elif not isinstance(target, ProjectRootTarget):
            raise NotFoundException("Unsupported repository target")

        self._surfaces.ensure_target_defaults(
            project_id=project_id,
            scope=scope,
            created_by=created_by,
        )
        scope_id = repository_target_scope_id(target)
        enabled = []
        for kind in ("git_remote", "cli"):
            row = self._surfaces.get_by_target_kind(project_id, scope_id, kind)
            if row is None:
                raise RuntimeError(f"{kind} Access Surface was not enabled")
            enabled.append(self._repo.get(str(row["id"])))
        return [connector for connector in enabled if connector is not None]

    def update(self, connector_id: str, patch: dict[str, Any]) -> Optional[Connector]:
        existing = self._repo.get(connector_id)
        if existing is None:
            return None
        # Refuse to flip a builtin's direction.
        if existing.is_builtin and "direction" in patch:
            raise BusinessException(
                "Built-in connector direction is fixed at 'bidirectional'."
            )
        # Target/provider identity is immutable after creation.
        for forbidden in ("provider", "target", "scope_id", "project_id"):
            patch.pop(forbidden, None)
        updated = self._repo.update(connector_id, patch)
        if updated is not None:
            _clear_connector_admission_cache(updated.scope_id, updated.provider)
        return updated

    def activate_agent_connector(self, connector_id: str) -> Optional[Connector]:
        """Activate the built-in chat Agent access surface for a target.

        The default AI Agent is an in-app chat runtime, not an external MCP
        endpoint. Activation marks an existing ``kind='agent'`` surface ready
        and writes its resolved repository view into config. ``/agent-config``
        then exposes the row
        as a normal saved Agent, and the frontend can open ``agent_chat``
        directly.
        """
        existing = self._repo.get(connector_id)
        if existing is None:
            return None
        if existing.provider != "agent":
            raise BusinessException("Only built-in agent connectors can be activated.")

        scope = None
        if isinstance(existing.target, ScopeTarget):
            scope = self._scopes.get(existing.target.scope_id)
            if scope is None or scope.project_id != existing.project_id:
                raise NotFoundException(
                    "Agent repository Scope not found",
                    code=ErrorCode.SCOPE_NOT_FOUND,
                )

        config = dict(existing.config or {})
        config.setdefault("name", existing.name or (scope.name if scope else "AI Agent"))
        config.setdefault("icon", "✨")
        config["type"] = "chat"
        config["activated"] = True
        config["repository_view"] = {
            "target": {
                "kind": "scope" if scope else "project_root",
                "project_id": existing.project_id,
                **({"scope_id": scope.id} if scope else {}),
            },
            "path_prefix": scope.path if scope else "",
            "excludes": scope.exclude if scope else [],
            "max_mode": scope.max_mode if scope else "rw",
        }
        config.setdefault(
            "bash_view",
            {
                "path_prefix": scope.path if scope else "",
                "excludes": scope.exclude if scope else [],
                "max_mode": scope.max_mode if scope else "rw",
            },
        )

        updated = self._repo.update(
            connector_id,
            {
                "config": config,
                "status": "active",
            },
        )
        if updated is not None:
            _clear_connector_admission_cache(updated.scope_id, updated.provider)
        return updated

    def delete(self, connector_id: str) -> None:
        existing = self._repo.get(connector_id)
        if existing is None:
            raise NotFoundException("Connector not found")
        if existing.is_builtin:
            raise BusinessException(
                "Standard Access surfaces are managed by their repository "
                "target. Pause the surface instead of deleting it through "
                "the connector API."
            )
        self._repo.delete(connector_id)

    def pause(self, connector_id: str) -> None:
        updated = self._repo.update(connector_id, {"status": "paused"})
        if updated is not None:
            _clear_connector_admission_cache(updated.scope_id, updated.provider)

    def resume(self, connector_id: str) -> None:
        updated = self._repo.update(connector_id, {"status": "active"})
        if updated is not None:
            _clear_connector_admission_cache(updated.scope_id, updated.provider)

    # ── Run orchestration ────────────────────────────────────────────────

    async def run_now(self, connector_id: str) -> Optional[str]:
        """Manually trigger a connector run. Returns the connector_run_id.

        Heavy lifting lives in platform/integrations/engine.py — we just
        kick it off here and return the run id.

        Built-in cli/agent/git_remote connectors don't have a "run now"
        semantic — they're conduits for the user's own writes, not pollers.
        The engine returns None for those; we surface a clear 400 here so
        the UI doesn't render a useless run button.
        """
        connector = self._repo.get(connector_id)
        if connector is None:
            raise NotFoundException("Connector not found")
        if connector.is_builtin:
            raise BusinessException(
                "Built-in Access surfaces don't have a "
                "manual run."
            )
        if connector.status == "paused":
            raise BusinessException("Connector is paused; resume it first")

        # Lazy imports to avoid pulling the heavy engine module on
        # read-only routes. Use the non-DI factory because we may be
        # called from background contexts (scheduled triggers) too.
        from src.platform.integrations.dependencies import create_integration_engine
        engine = create_integration_engine()
        run_id = await engine.execute_for_connector(connector)
        return run_id
