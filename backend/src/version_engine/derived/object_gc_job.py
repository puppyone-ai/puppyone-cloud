"""Scheduled Git object GC job."""

from __future__ import annotations

from src.version_engine.derived.object_gc_worker import process_object_gc_projects
from src.utils.logger import log_error


def process_git_object_gc() -> dict:
    try:
        results = process_object_gc_projects()
        return {
            "status": "ok",
            "projects": len(results),
            "unreachable": sum(r.unreachable_count for r in results),
            "eligible": sum(r.eligible_count for r in results),
            "quarantined": sum(r.quarantined_count for r in results),
            "deleted": sum(r.deleted_count for r in results),
            "dry_run": results[0].dry_run if results else True,
        }
    except Exception as exc:
        log_error(f"[object-gc] scheduler job failed: {exc}")
        return {"status": "failed", "error": str(exc)}
