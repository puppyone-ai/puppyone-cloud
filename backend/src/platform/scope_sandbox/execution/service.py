"""
Sandbox service - ephemeral execution mode of the unified sandbox subsystem

Automatically selects E2B cloud sandbox or Docker local sandbox based on configuration.

Configuration (in backend/src/config.py):
- SANDBOX_TYPE: "e2b" | "docker" | "auto"
  - "e2b": Use E2B cloud sandbox (requires E2B_API_KEY)
  - "docker": Use local Docker container sandbox
  - "auto": Auto-select (use E2B if E2B_API_KEY exists, otherwise use Docker)
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from src.platform.project.write_lease import ProjectWriteLease, ProjectWriteLeaseFactory

from .base import SandboxBase

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY))\s*=\s*([^\s;&|]+)"
)
logger = logging.getLogger(__name__)


def _audit_command_preview(command: str) -> str:
    """Bound and redact command text before it enters durable audit storage."""
    preview = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", command or "")
    return preview[:512]


class SandboxService:
    """
    Unified sandbox service interface

    Acts as a facade/proxy class, delegating to concrete sandbox implementations (E2B or Docker).
    Supports automatic backend switching via configuration or environment variables.
    """

    def __init__(
        self,
        sandbox_impl: SandboxBase | None = None,
        sandbox_factory: Callable[[], Any] | None = None,
        write_lease_factory: ProjectWriteLeaseFactory = ProjectWriteLease,
    ):
        """
        Initialize sandbox service

        Args:
            sandbox_impl: Directly provide a sandbox implementation (for testing or forced selection)
            sandbox_factory: E2B sandbox factory (backward compatible, for testing)
        """
        if sandbox_impl is not None:
            self._impl = sandbox_impl
        elif sandbox_factory is not None:
            # Backward compatible: create E2B sandbox using custom factory
            from .e2b_sandbox import E2BSandbox

            self._impl = E2BSandbox(sandbox_factory=sandbox_factory)
        else:
            # Auto-create based on configuration
            self._impl = _create_sandbox_impl()

        # A key's presence means start() already made the billing decision.
        # None means billing was disabled or a shadow reservation failed.
        self._billing_runs: dict[str, str | None] = {}
        self._billing_timeout_tasks: dict[str, asyncio.Task[None]] = {}
        self._write_lease_factory = write_lease_factory

    def _session_timeout_seconds(self) -> int:
        return max(1, int(getattr(self._impl, "_session_timeout", 600)))

    def _cancel_billing_timeout(self, session_id: str) -> None:
        task = self._billing_timeout_tasks.pop(session_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _arm_billing_timeout(self, session_id: str) -> None:
        self._cancel_billing_timeout(session_id)

        async def expire() -> None:
            try:
                await asyncio.sleep(self._session_timeout_seconds())
                await self._impl.stop(session_id)
                await self._finish_billing(session_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Provider and Runtime recovery both retain durable state; the
                # cross-worker reapers can safely retry this cleanup.
                logger.exception(
                    "sandbox_billing_timeout_cleanup_failed",
                    extra={"session_id": session_id},
                )

        self._billing_timeout_tasks[session_id] = asyncio.create_task(expire())

    async def _begin_billing(
        self,
        session_id: str,
        audit_context: dict[str, Any] | None,
    ) -> str | None:
        from src.platform.billing.runtime import get_runtime_metering_service

        context = {
            **(audit_context or {}),
            "session_id": session_id,
            "run_id": f"sandbox-session:{session_id}",
        }
        if not context.get("maximum_runtime_units"):
            # Provider sessions have a hard lifetime. Reserve that upper bound
            # so settlement can never exceed the authorized units merely
            # because an Agent used the entire provider timeout.
            timeout_seconds = self._session_timeout_seconds()
            context["maximum_runtime_units"] = max(1, timeout_seconds // 60 + 1)
        run_id = await get_runtime_metering_service().start_session(audit_context=context)
        self._billing_runs[session_id] = run_id
        return run_id

    async def _cancel_billing(self, session_id: str) -> None:
        from src.platform.billing.runtime import get_runtime_metering_service

        self._cancel_billing_timeout(session_id)
        run_id = self._billing_runs.pop(session_id, None)
        if not run_id:
            return
        try:
            await get_runtime_metering_service().cancel_session(run_id)
        except Exception:
            # The durable run is retried by the billing recovery loop. Never
            # replace the provider's start error with compensation noise.
            logger.exception("sandbox_runtime_cancellation_failed", extra={"run_id": run_id})

    async def _finish_billing(self, session_id: str) -> None:
        from src.platform.billing.runtime import get_runtime_metering_service

        self._cancel_billing_timeout(session_id)
        run_id = self._billing_runs.pop(session_id, None)
        if not run_id:
            return
        try:
            await get_runtime_metering_service().finish_session(run_id)
        except Exception:
            logger.exception("sandbox_runtime_settlement_failed", extra={"run_id": run_id})

    async def start(
        self,
        session_id: str,
        data: Any,
        readonly: bool,
        *,
        audit_context: dict[str, Any] | None = None,
    ) -> dict:
        """Create a sandbox session and preload a single JSON data"""
        project_id = str((audit_context or {}).get("project_id") or "")

        async def admitted_start() -> dict:
            await self._begin_billing(session_id, audit_context)
            try:
                if project_id:
                    result = await self._impl.start(
                        session_id,
                        data,
                        readonly,
                        project_id=project_id,
                    )
                else:
                    result = await self._impl.start(session_id, data, readonly)
            except Exception:
                await self._cancel_billing(session_id)
                raise
            if not result.get("success"):
                await self._cancel_billing(session_id)
            else:
                self._arm_billing_timeout(session_id)
            return result

        if not project_id:
            return await admitted_start()
        async with self._write_lease_factory(project_id, "sandbox.execution.start"):
            return await admitted_start()

    async def start_with_files(
        self,
        session_id: str,
        files: list,
        readonly: bool,
        s3_service: Any | None = None,
        *,
        audit_context: dict[str, Any] | None = None,
    ) -> dict:
        """Create a sandbox session and preload multiple files"""
        project_id = str((audit_context or {}).get("project_id") or "")

        async def admitted_start() -> dict:
            await self._begin_billing(session_id, audit_context)
            try:
                if project_id:
                    result = await self._impl.start_with_files(
                        session_id,
                        files,
                        readonly,
                        s3_service,
                        project_id=project_id,
                    )
                else:
                    result = await self._impl.start_with_files(
                        session_id, files, readonly, s3_service
                    )
            except Exception:
                await self._cancel_billing(session_id)
                raise
            if not result.get("success"):
                await self._cancel_billing(session_id)
            else:
                self._arm_billing_timeout(session_id)
            return result

        if not project_id:
            return await admitted_start()
        async with self._write_lease_factory(
            project_id, "sandbox.execution.start_with_files"
        ):
            return await admitted_start()

    async def exec(
        self,
        session_id: str,
        command: str,
        *,
        audit_context: dict[str, Any] | None = None,
    ) -> dict:
        """Execute a command in the sandbox.

        Command-safety policy is enforced here (ISSUE-009) so every caller —
        the sandbox HTTP endpoint and the agent bash tool alike — passes the
        same blacklist. Defense-in-depth over the container boundary (ISSUE-010).
        """
        from src.platform.analytics.service import log_bash_execution

        from ..execution_policy import SandboxCommandRejected, assert_command_allowed

        context = audit_context or {}
        started = time.monotonic()
        try:
            assert_command_allowed(command)
        except SandboxCommandRejected:
            await log_bash_execution(
                command=_audit_command_preview(command),
                user_id=context.get("user_id"),
                agent_id=context.get("agent_id"),
                session_id=context.get("session_id"),
                sandbox_session_id=session_id,
                success=False,
                error_message="Command rejected by sandbox policy",
                latency_ms=int((time.monotonic() - started) * 1000),
                source=context.get("source", "agent"),
                decision="rejected",
            )
            raise

        try:
            if session_id in self._billing_runs:
                result = await self._impl.exec(session_id, command)
            else:
                # Compatibility for third-party callers not yet passing context
                # at start(). First-party hosted paths meter the full session.
                from src.platform.billing.runtime import get_runtime_metering_service

                result = await get_runtime_metering_service().execute(
                    audit_context={
                        **context,
                        "session_id": context.get("session_id") or session_id,
                    },
                    operation=lambda: self._impl.exec(session_id, command),
                )
        except Exception as exc:
            await log_bash_execution(
                command=_audit_command_preview(command),
                user_id=context.get("user_id"),
                agent_id=context.get("agent_id"),
                session_id=context.get("session_id"),
                sandbox_session_id=session_id,
                success=False,
                error_message=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
                source=context.get("source", "agent"),
                decision="allowed",
            )
            raise

        success = bool(result.get("success"))
        await log_bash_execution(
            command=_audit_command_preview(command),
            user_id=context.get("user_id"),
            agent_id=context.get("agent_id"),
            session_id=context.get("session_id"),
            sandbox_session_id=session_id,
            success=success,
            output=result.get("output") if success else None,
            error_message=None if success else str(result.get("error") or "Execution failed"),
            latency_ms=int((time.monotonic() - started) * 1000),
            source=context.get("source", "agent"),
            decision="allowed",
        )
        return result

    async def read(self, session_id: str) -> dict:
        """Read the contents of /workspace/data.json"""
        return await self._impl.read(session_id)

    async def read_file(self, session_id: str, path: str, parse_json: bool = False) -> dict:
        """Read a file at the specified path in the sandbox"""
        return await self._impl.read_file(session_id, path, parse_json)

    async def stop(self, session_id: str) -> dict:
        """Stop and clean up a sandbox session"""
        result = await self._impl.stop(session_id)
        if result.get("success"):
            await self._finish_billing(session_id)
        return result

    async def status(self, session_id: str) -> dict:
        """Get sandbox session status"""
        return await self._impl.status(session_id)

    async def stop_all(self) -> None:
        """Stop all sandbox sessions"""
        await self._impl.stop_all()
        for session_id in list(self._billing_runs):
            await self._finish_billing(session_id)

    @property
    def sandbox_type(self) -> str:
        """Return the currently used sandbox type"""
        from .docker_sandbox import DockerSandbox
        from .e2b_sandbox import E2BSandbox

        if isinstance(self._impl, E2BSandbox):
            return "e2b"
        elif isinstance(self._impl, DockerSandbox):
            return "docker"
        else:
            return "unknown"


def _create_sandbox_impl() -> SandboxBase:
    """
    Create sandbox implementation based on configuration

    Priority:
    1. SANDBOX_TYPE from configuration
    2. In auto mode, detect whether E2B_API_KEY exists
    """
    from src.config import settings

    sandbox_type = settings.SANDBOX_TYPE

    # Auto mode: detect environment
    if sandbox_type == "auto":
        if settings.E2B_API_KEY:
            sandbox_type = "e2b"
            print("[SandboxService] Auto-detected E2B_API_KEY, using E2B sandbox")
        else:
            sandbox_type = "docker"
            print("[SandboxService] No E2B_API_KEY found, using Docker sandbox")

    # Create the corresponding implementation
    if sandbox_type == "e2b":
        if not settings.E2B_API_KEY:
            raise RuntimeError("E2B_API_KEY is required for E2B sandbox execution")
        from .e2b_sandbox import E2BSandbox

        print("[SandboxService] Initializing E2B cloud sandbox")
        return E2BSandbox()
    else:
        from .docker_sandbox import DockerSandbox

        print("[SandboxService] Initializing Docker local sandbox")
        return DockerSandbox()


def get_sandbox_type() -> str:
    """
    Get the sandbox type that will be used (without creating an instance)

    Used by the frontend to query the current configuration
    """
    from src.config import settings

    sandbox_type = settings.SANDBOX_TYPE

    if sandbox_type == "auto":
        if settings.APP_ENV in {"staging", "production"}:
            if not settings.E2B_API_KEY:
                raise RuntimeError("Hosted sandbox cannot fall back to Docker")
            return "e2b"
        if settings.E2B_API_KEY:
            return "e2b"
        else:
            return "docker"

    return sandbox_type
