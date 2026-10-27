"""Readiness belongs to the connection which will serve files.

The lifecycle worker persists checking_files after compute/initialization. A
consumer proves the native service on its own retained channel, then completes
the same durable operation under the current authorization revision.
"""

import asyncio
import copy
import time
from datetime import UTC, datetime

from src.infra.execution.runner import run_blocking
from src.platform.sandbox import workspace_event, workspace_stage

from .errors import WorkspaceError
from .execution.worker_port import WorkerDisconnected
from .opening_wait import OpeningObservers


def awaiting_files(row):
    return bool(
        row["state"] in {"allocating", "resuming"}
        and row.get("open_requested")
        and row.get("operation_phase") == "checking_files"
        and not row.get("operation_error")
        and all(
            row.get(key) and datetime.fromisoformat(row[key]) > datetime.now(UTC)
            for key in ("open_deadline", "operation_until")
        )
    )


def same_runtime(left, right):
    return bool(right) and all(
        left.get(key) == right.get(key)
        for key in (
            "id",
            "user_id",
            "project_id",
            "generation",
            "compute_epoch",
            "binding",
            "resource",
        )
    )


class WorkspaceOpening:
    def __init__(self, service):
        self.service = service
        self.observers = OpeningObservers()

    async def status(self, user, project, *, wait_seconds=0):
        """Observe an existing intent; never create or wake a workspace.

        Finishing through the existing status endpoint also supports desktops
        released before the consumer handoff protocol.
        """
        if wait_seconds:
            return await self.observers.observe(
                (user, project, wait_seconds),
                lambda: self._observe(user, project, wait_seconds),
            )
        return await self._observe(user, project, 0)

    async def _prepare_connection(self, row):
        host = self.service.channel_factory(row, purpose="files")
        try:
            if not host.runtime.capabilities.connection_preparation:
                return None
            async with asyncio.timeout(30):
                return await host.runtime.prepare_connection(host)
        finally:
            await host.runtime.aclose()

    async def _observe(self, user, project, wait_seconds):
        from src.platform.authorization.models import ProjectAction

        deadline = time.monotonic() + wait_seconds
        preparation, prepared_row = None, None
        try:
            while True:
                started = time.monotonic()
                context, _ = await run_blocking(
                    self.service.context, user, project, ProjectAction.CONTENT_READ
                )
                row = context["workbench"]
                if preparation and (
                    not same_runtime(prepared_row, row)
                    or prepared_row.get("operation_id") != row.get("operation_id")
                ):
                    preparation.cancel()
                    await asyncio.gather(preparation, return_exceptions=True)
                    preparation, prepared_row = None, None
                if row and awaiting_files(row):
                    async with asyncio.timeout(35):
                        connection = None
                        if preparation:
                            try:
                                connection = await preparation
                            except Exception:
                                # Speculation is optional. The normal consumer path
                                # re-inspects and verifies identity before access.
                                workspace_event("connection.preparation_failed")
                        return await self.prepare(
                            user, project, row["generation"], context=context, connection=connection
                        )
                active = bool(
                    row
                    and row.get("open_requested")
                    and not row.get("operation_error")
                    and row.get("open_deadline")
                    and datetime.fromisoformat(row["open_deadline"]) > datetime.now(UTC)
                    and row["state"] not in {"running", "retained", "cleanup_pending"}
                )
                remaining = deadline - time.monotonic()
                if not active or remaining <= 0:
                    return row, None
                if (
                    preparation is None
                    and row["state"] == "resuming"
                    and row.get("resource")
                    and row.get("operation_id")
                    and row.get("operation_phase") == "connecting"
                    and row.get("operation_until")
                    and datetime.fromisoformat(row["operation_until"]) > datetime.now(UTC)
                ):
                    self.service.authorization.authorize_facts(
                        context["facts"], project, user, ProjectAction.AGENT_RUN
                    )
                    prepared_row = copy.deepcopy(row)
                    preparation = asyncio.create_task(self._prepare_connection(prepared_row))
                # Include RPC latency in the cadence; do not sleep another second
                # after every round trip. No DB connection is held while waiting.
                await asyncio.sleep(min(remaining, max(0, 1 - (time.monotonic() - started))))
        finally:
            if preparation:
                preparation.cancel()
                await asyncio.gather(preparation, return_exceptions=True)

    async def prepare(self, user, project, generation, *, context=None, connection=None):
        from src.platform.authorization.models import ProjectAction

        from .lifecycle import meter_id

        service = self.service
        if context is None:
            with workspace_stage("opening.admission"):
                context, _ = await run_blocking(
                    service.context, user, project, ProjectAction.CONTENT_READ
                )
        row = context["workbench"]
        if not row or row["generation"] != generation:
            raise WorkspaceError(409, {"code": "workbench_generation_changed"})
        if row["state"] == "running":
            # This uses the regular fenced file path and its final authorization.
            root = await service.file_operation(
                user, project, generation, {"action": "list", "path": ""}
            )
            return row, root
        if not awaiting_files(row):
            raise WorkspaceError(409, {"code": "workbench_not_ready"})
        service.authorization.authorize_facts(
            context["facts"], project, user, ProjectAction.AGENT_RUN
        )
        async with asyncio.timeout(35):
            await service.billing.check_session(meter_id(row))
            options = {"connection": connection} if connection is not None else {}
            async with service.channels.borrow(row, **options) as host:
                with workspace_stage("opening.root"):
                    root = await service.filesystem.operation(host, {"action": "list", "path": ""})
                with workspace_stage("opening.confirm"):
                    current, _ = await run_blocking(
                        service.context, user, project, ProjectAction.AGENT_RUN
                    )
                    if not same_runtime(row, current["workbench"]):
                        raise WorkerDisconnected("Workspace changed during opening")
                    row = await run_blocking(
                        service.repository.finish_open, row, current["revision"]
                    )
            return row, root
