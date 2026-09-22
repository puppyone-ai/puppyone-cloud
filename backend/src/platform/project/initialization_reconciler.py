"""Resume durable Project root initialization through the existing L5 API."""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from src.config import settings
from src.platform.project.control_plane import ProjectControlPlaneRepository
from src.platform.project.orchestration import initialize_project_tree_sync
from src.platform.project.write_lease import ProjectWriteLease, ProjectWriteLeaseFactory
from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container
from src.version_engine.write_engine.engine import VersionWriteEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectInitializationSummary:
    claimed: int = 0
    completed: int = 0
    aborted: int = 0
    dead_lettered: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "completed": self.completed,
            "aborted": self.aborted,
            "dead_lettered": self.dead_lettered,
            "failed": self.failed,
        }


class ProjectInitializationReconciler:
    def __init__(
        self,
        repository: ProjectControlPlaneRepository,
        version_engine: VersionWriteEngine,
        *,
        worker_id: str | None = None,
        max_attempts: int | None = None,
        write_lease_factory: ProjectWriteLeaseFactory = ProjectWriteLease,
    ):
        self._repository = repository
        self._version_engine = version_engine
        self._worker_id = worker_id or f"{socket.gethostname()}:{uuid4()}"
        self._max_attempts = max(
            1,
            max_attempts or settings.PROJECT_INITIALIZATION_MAX_ATTEMPTS,
        )
        self._write_lease_factory = write_lease_factory

    async def _terminalize_empty_initialization(
        self,
        *,
        operation_key: str,
        actor_user_id: str,
        project_id: str,
        error: str,
    ) -> ProjectInitializationSummary:
        outcome = await asyncio.to_thread(
            self._repository.abandon_initialization,
            project_id=project_id,
            operation_key=operation_key,
            actor_user_id=actor_user_id,
            quiescence_seconds=settings.PROJECT_DELETION_QUIESCENCE_SECONDS,
            worker_id=self._worker_id,
        )
        result = str(outcome.get("outcome") or "")
        if result in {"accepted", "replayed", "gone"}:
            return ProjectInitializationSummary(claimed=1, aborted=1)
        if result != "claim_lost":
            return await self._dead_letter_claim(
                operation_key=operation_key,
                actor_user_id=actor_user_id,
                project_id=project_id,
                error=(
                    f"{error}; terminal abandonment outcome: {result or 'missing'}"
                ),
            )
        raise RuntimeError("Project initialization claim was lost during terminalization")

    async def _dead_letter_claim(
        self,
        *,
        operation_key: str,
        actor_user_id: str,
        project_id: str,
        error: str,
    ) -> ProjectInitializationSummary:
        recorded = await asyncio.to_thread(
            self._repository.dead_letter_initialization,
            operation_key=operation_key,
            actor_user_id=actor_user_id,
            worker_id=self._worker_id,
            error=error,
        )
        if not recorded:
            raise RuntimeError("Project initialization claim was lost before dead-lettering")
        logger.error(
            "Project initialization dead-lettered project_id=%s operation_key=%s error=%s",
            project_id,
            operation_key,
            error,
        )
        return ProjectInitializationSummary(claimed=1, dead_lettered=1)

    async def _record_failure(
        self,
        *,
        operation_key: str,
        actor_user_id: str,
        attempts: int,
        error: str,
    ) -> ProjectInitializationSummary:
        await asyncio.to_thread(
            self._repository.fail_initialization,
            operation_key=operation_key,
            actor_user_id=actor_user_id,
            worker_id=self._worker_id,
            error=error,
            retry_after_seconds=min(3600, 15 * (2 ** min(attempts - 1, 8))),
        )
        return ProjectInitializationSummary(claimed=1, failed=1)

    async def run_once(
        self,
        *,
        lease_seconds: int = 300,
    ) -> ProjectInitializationSummary:
        operations = await asyncio.to_thread(
            self._repository.claim_initializations,
            worker_id=self._worker_id,
            limit=1,
            lease_seconds=max(30, lease_seconds),
        )
        if not operations:
            return ProjectInitializationSummary()

        operation = operations[0]
        operation_key = str(operation.get("operation_key") or "")
        actor_user_id = str(operation.get("actor_user_id") or "")
        project_id = str(operation.get("project_id") or "")
        attempts = max(1, int(operation.get("initialization_attempts") or 1))
        publication_mode = str(operation.get("publication_mode") or "empty")
        deadline_expired = _deadline_expired(operation.get("initialization_deadline_at"))
        try:
            if not operation_key or not actor_user_id or not project_id:
                raise RuntimeError("Project initialization operation is malformed")
            if publication_mode == "deferred":
                outcome = await asyncio.to_thread(
                    self._repository.abort_deferred_publication,
                    project_id=project_id,
                    operation_key=operation_key,
                    actor_user_id=actor_user_id,
                    quiescence_seconds=settings.PROJECT_DELETION_QUIESCENCE_SECONDS,
                    worker_id=self._worker_id,
                )
                if str(outcome.get("outcome") or "") not in {
                    "accepted",
                    "replayed",
                    "gone",
                }:
                    raise RuntimeError(
                        "Deferred Project publication abort was not acknowledged: "
                        f"{outcome.get('outcome') or 'missing'}"
                    )
                return ProjectInitializationSummary(claimed=1, aborted=1)
            if publication_mode != "empty":
                raise RuntimeError(
                    f"Unknown Project publication mode: {publication_mode or 'missing'}"
                )
            if deadline_expired:
                return await self._terminalize_empty_initialization(
                    operation_key=operation_key,
                    actor_user_id=actor_user_id,
                    project_id=project_id,
                    error="Project initialization deadline expired before publication",
                )
            async with self._write_lease_factory(
                project_id,
                "project.reconcile_initialization",
                initialization_operation_key=operation_key,
                initialization_actor=actor_user_id,
                initialization_worker=self._worker_id,
            ):
                await asyncio.to_thread(
                    initialize_project_tree_sync,
                    self._version_engine,
                    project_id,
                )
                outcome = await asyncio.to_thread(
                    self._repository.complete_initialization,
                    project_id=project_id,
                    operation_key=operation_key,
                    actor_user_id=actor_user_id,
                )
            if str(outcome.get("outcome") or "") not in {"completed", "replayed"}:
                raise RuntimeError(
                    "Project initialization completion was not acknowledged: "
                    f"{outcome.get('outcome') or 'missing'}"
                )
            return ProjectInitializationSummary(claimed=1, completed=1)
        except Exception as exc:
            if publication_mode == "deferred":
                try:
                    return await self._dead_letter_claim(
                        operation_key=operation_key,
                        actor_user_id=actor_user_id,
                        project_id=project_id,
                        error=f"Deferred publication cleanup failed: {exc}",
                    )
                except Exception as terminal_error:
                    exc = RuntimeError(f"{exc}; dead-letter failed: {terminal_error}")
            if publication_mode == "empty" and attempts >= self._max_attempts:
                try:
                    return await self._terminalize_empty_initialization(
                        operation_key=operation_key,
                        actor_user_id=actor_user_id,
                        project_id=project_id,
                        error=str(exc),
                    )
                except Exception as terminal_error:
                    try:
                        return await self._dead_letter_claim(
                            operation_key=operation_key,
                            actor_user_id=actor_user_id,
                            project_id=project_id,
                            error=f"{exc}; terminal cleanup failed: {terminal_error}",
                        )
                    except Exception as dead_letter_error:
                        exc = RuntimeError(
                            f"{exc}; terminalization failed: {terminal_error}; "
                            f"dead-letter failed: {dead_letter_error}"
                        )
            return await self._record_failure(
                operation_key=operation_key,
                actor_user_id=actor_user_id,
                attempts=attempts,
                error=str(exc),
            )


def _deadline_expired(value: object) -> bool:
    """Treat a missing/malformed durable deadline as expired, never immortal."""

    try:
        deadline = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return deadline <= datetime.now(UTC)
    except (TypeError, ValueError):
        return True


async def process_project_initialization_reconciliation() -> dict[str, int | str]:
    container = build_worker_version_engine_container()
    reconciler = ProjectInitializationReconciler(
        ProjectControlPlaneRepository(),
        container.write_engine(),
        max_attempts=settings.PROJECT_INITIALIZATION_MAX_ATTEMPTS,
    )
    summary = await reconciler.run_once(
        lease_seconds=settings.PROJECT_INITIALIZATION_RECONCILE_LEASE_SECONDS,
    )
    return {"status": "ok", **summary.as_dict()}
