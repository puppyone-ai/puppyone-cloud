"""Readiness belongs to the connection which will serve files.

The lifecycle worker persists checking_files after compute/initialization. A
consumer proves the native service on its own retained channel, then completes
the same durable operation under the current authorization revision.
"""

import asyncio
from datetime import UTC, datetime

from .errors import WorkspaceError
from .execution.timing import workspace_stage
from .execution.worker_port import WorkerDisconnected


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

    async def status(self, user, project):
        """Observe an existing intent; never create or wake a workspace.

        Finishing through the existing status endpoint also supports desktops
        released before the consumer handoff protocol.
        """
        from src.platform.authorization.models import ProjectAction

        context, _ = await asyncio.to_thread(
            self.service.context, user, project, ProjectAction.CONTENT_READ
        )
        row = context["workbench"]
        if row and awaiting_files(row):
            return await self.prepare(user, project, row["generation"], context=context)
        return row, None

    async def prepare(self, user, project, generation, *, context=None):
        from src.platform.authorization.models import ProjectAction

        from .lifecycle import meter_id

        service = self.service
        if context is None:
            with workspace_stage("opening.admission"):
                context, _ = await asyncio.to_thread(
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
            async with service.channels.borrow(row) as host:
                with workspace_stage("opening.root"):
                    root = await service.filesystem.operation(host, {"action": "list", "path": ""})
                with workspace_stage("opening.confirm"):
                    current, _ = await asyncio.to_thread(
                        service.context, user, project, ProjectAction.AGENT_RUN
                    )
                    if not same_runtime(row, current["workbench"]):
                        raise WorkerDisconnected("Workspace changed during opening")
                    row = await asyncio.to_thread(
                        service.repository.finish_open, row, current["revision"]
                    )
            return row, root
