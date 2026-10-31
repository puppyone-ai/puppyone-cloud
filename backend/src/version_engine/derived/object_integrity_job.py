"""Scheduled primary-loose-object integrity scan job (runbook §8①)."""

from __future__ import annotations

from src.version_engine.derived.object_integrity_worker import (
    process_object_integrity_projects,
)
from src.utils.logger import log_error


def process_object_integrity_scan() -> dict:
    try:
        results = process_object_integrity_projects()
        return {
            "status": "ok",
            "projects": len(results),
            "checked": sum(r.checked for r in results),
            "corrupt": sum(len(r.corrupt) for r in results),
            "healed": sum(r.healed for r in results),
        }
    except Exception as exc:
        log_error(f"[integrity-scan] scheduler job failed: {exc}")
        return {"status": "failed", "error": str(exc)}
