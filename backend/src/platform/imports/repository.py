"""Repository for durable one-time import jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from src.infra.supabase.client import SupabaseClient
from src.platform.imports.schemas import ImportJobResponse, ImportJobStatus


ACTIVE_STATUSES = (
    ImportJobStatus.QUEUED.value,
    ImportJobStatus.RUNNING.value,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _job_is_stale(
    *, status: str, started_at: str | None, created_at: str | None,
    updated_at: str | None, cutoff: datetime,
) -> bool:
    """True when an ACTIVE import job's last meaningful activity predates cutoff.

    RUNNING jobs are judged by ``started_at`` (when work began), QUEUED jobs by
    ``created_at`` (when they entered the lane); ``updated_at``/``created_at`` are
    fallbacks. With no usable timestamp we never reap (fail-safe). Callers must
    set ``cutoff`` above the worker ``job_timeout`` so a still-live job — which
    ARQ kills at job_timeout — can never be judged stale.
    """
    ref = _parse_dt(started_at if status == ImportJobStatus.RUNNING.value else created_at)
    if ref is None:
        ref = _parse_dt(updated_at) or _parse_dt(created_at)
    if ref is None:
        return False
    return ref < cutoff


@dataclass
class ImportJob:
    id: str
    project_id: str
    created_by: str
    provider: str
    source_url: str
    source_kind: str | None = None
    source_ref: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    org_id: str | None = None
    name: str | None = None
    target_path: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    status: str = ImportJobStatus.QUEUED.value
    phase: str = "queued"
    progress: int = 0
    message: str | None = None
    result_path: str | None = None
    result_commit_id: str | None = None
    error_message: str | None = None
    worker_job_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ImportJob":
        return cls(
            id=str(row["id"]),
            org_id=row.get("org_id"),
            project_id=str(row["project_id"]),
            created_by=str(row["created_by"]),
            provider=str(row["provider"]),
            source_url=str(row["source_url"]),
            source_kind=row.get("source_kind"),
            source_ref=row.get("source_ref") or {},
            idempotency_key=row.get("idempotency_key"),
            name=row.get("name"),
            target_path=row.get("target_path") or "",
            config=row.get("config") or {},
            status=row.get("status") or ImportJobStatus.QUEUED.value,
            phase=row.get("phase") or "queued",
            progress=int(row.get("progress") or 0),
            message=row.get("message"),
            result_path=row.get("result_path"),
            result_commit_id=row.get("result_commit_id"),
            error_message=row.get("error_message"),
            worker_job_id=row.get("worker_job_id"),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def to_response(self) -> ImportJobResponse:
        return ImportJobResponse.model_validate({
            "id": self.id,
            "org_id": self.org_id,
            "project_id": self.project_id,
            "created_by": self.created_by,
            "provider": self.provider,
            "source_url": self.source_url,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "idempotency_key": self.idempotency_key,
            "name": self.name,
            "target_path": self.target_path,
            "config": self.config,
            "status": self.status,
            "phase": self.phase,
            "progress": self.progress,
            "message": self.message,
            "result_path": self.result_path,
            "result_commit_id": self.result_commit_id,
            "error_message": self.error_message,
            "worker_job_id": self.worker_job_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })


class ImportJobRepository:
    TABLE = "import_jobs"

    def __init__(self, supabase_client: SupabaseClient | None = None):
        self.client = (supabase_client or SupabaseClient()).client

    def create(
        self,
        *,
        org_id: str | None,
        project_id: str,
        created_by: str,
        provider: str,
        source_url: str,
        source_kind: str | None = None,
        source_ref: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        name: str | None = None,
        target_path: str = "",
        config: dict[str, Any] | None = None,
    ) -> ImportJob:
        row = {
            "id": str(uuid.uuid4()),
            "org_id": org_id,
            "project_id": project_id,
            "created_by": created_by,
            "provider": provider,
            "source_url": source_url,
            "source_kind": source_kind,
            "source_ref": source_ref or {},
            "idempotency_key": idempotency_key,
            "name": name,
            "target_path": target_path or "",
            "config": config or {},
            "status": ImportJobStatus.QUEUED.value,
            "phase": "queued",
            "progress": 0,
            "message": "Queued",
        }
        resp = self.client.table(self.TABLE).insert(row).execute()
        return ImportJob.from_row(resp.data[0])

    def get(self, job_id: str) -> ImportJob | None:
        resp = (
            self.client.table(self.TABLE)
            .select("*")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return ImportJob.from_row(rows[0]) if rows else None

    def get_by_idempotency_key(
        self,
        *,
        project_id: str,
        provider: str,
        idempotency_key: str,
    ) -> ImportJob | None:
        resp = (
            self.client.table(self.TABLE)
            .select("*")
            .eq("project_id", project_id)
            .eq("provider", provider)
            .eq("idempotency_key", idempotency_key)
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return ImportJob.from_row(rows[0]) if rows else None

    def list_by_project(
        self,
        project_id: str,
        *,
        active_only: bool = False,
        limit: int = 20,
    ) -> list[ImportJob]:
        query = (
            self.client.table(self.TABLE)
            .select("*")
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if active_only:
            query = query.in_("status", [
                ImportJobStatus.QUEUED.value,
                ImportJobStatus.RUNNING.value,
            ])
        rows = query.execute().data or []
        return [ImportJob.from_row(row) for row in rows]

    def update(
        self,
        job_id: str,
        *,
        active_only: bool = False,
        **fields: Any,
    ) -> ImportJob | None:
        patch = {key: value for key, value in fields.items() if value is not None}
        if not patch:
            return self.get(job_id)
        query = (
            self.client.table(self.TABLE)
            .update(patch)
            .eq("id", job_id)
        )
        if active_only:
            query = query.in_("status", list(ACTIVE_STATUSES))
        resp = query.execute()
        rows = resp.data or []
        return ImportJob.from_row(rows[0]) if rows else self.get(job_id)

    def mark_running(
        self,
        job_id: str,
        *,
        phase: str,
        progress: int,
        message: str,
    ) -> ImportJob | None:
        return self.update(
            job_id,
            active_only=True,
            status=ImportJobStatus.RUNNING.value,
            phase=phase,
            progress=progress,
            message=message,
            error_message="",
            started_at=_now(),
        )

    def mark_completed(
        self,
        job_id: str,
        *,
        result_path: str | None,
        result_commit_id: str | None,
        message: str,
    ) -> ImportJob | None:
        return self.update(
            job_id,
            active_only=True,
            status=ImportJobStatus.COMPLETED.value,
            phase="completed",
            progress=100,
            message=message,
            result_path=result_path,
            result_commit_id=result_commit_id,
            error_message="",
            completed_at=_now(),
        )

    def mark_failed(self, job_id: str, error_message: str) -> ImportJob | None:
        return self.update(
            job_id,
            active_only=True,
            status=ImportJobStatus.FAILED.value,
            phase="failed",
            progress=100,
            message="Import failed",
            error_message=error_message[:10_000],
            completed_at=_now(),
        )

    def mark_cancelled(self, job_id: str) -> ImportJob | None:
        return self.update(
            job_id,
            active_only=True,
            status=ImportJobStatus.CANCELLED.value,
            phase="cancelled",
            progress=100,
            message="Import cancelled",
            completed_at=_now(),
        )

    def recover_stale_active_jobs(
        self, *, stale_seconds: int, limit: int = 100,
    ) -> list[ImportJob]:
        """Fail active import jobs whose worker never finished them.

        A live import cannot exceed the worker ``job_timeout`` (ARQ cancels it,
        which marks it failed); so an active row still untouched after
        ``stale_seconds`` (set above job_timeout) means the worker process died
        or the job was never consumed. Without this such rows sit QUEUED/RUNNING
        forever. ``mark_failed`` uses ``active_only`` so a job that finishes
        between the scan and the write is never clobbered.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(1, stale_seconds))
        resp = (
            self.client.table(self.TABLE)
            .select("*")
            .in_("status", list(ACTIVE_STATUSES))
            .order("created_at", desc=False)
            .limit(max(1, limit) * 4)
            .execute()
        )
        recovered: list[ImportJob] = []
        for row in (resp.data or []):
            job = ImportJob.from_row(row)
            if not _job_is_stale(
                status=job.status, started_at=job.started_at,
                created_at=job.created_at, updated_at=job.updated_at, cutoff=cutoff,
            ):
                continue
            updated = self.mark_failed(
                job.id,
                f"import did not complete within {stale_seconds}s; "
                f"worker presumed dead (reaped)",
            )
            if updated and updated.status == ImportJobStatus.FAILED.value:
                recovered.append(updated)
            if len(recovered) >= limit:
                break
        return recovered
