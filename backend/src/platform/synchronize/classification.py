"""Whether a stored binding may be managed as a Synchronize binding."""

from __future__ import annotations

from fastapi import HTTPException, status


def ensure_classified_binding(connection) -> None:
    # Final storage ownership is authoritative. Only an explicitly retained,
    # disabled historical binding may carry a retired provider classification.
    # Corrupt/unmigrated input still fails closed; no provider-based filtering.
    reason = getattr(connection, "legacy_read_only_reason", None)
    if isinstance(reason, str) and reason.strip() and connection.status == "disabled":
        return
    config = getattr(connection, "config", None) or {}
    if (
        getattr(connection, "provider", None) == "database"
        or "db_config" in config
        or (getattr(connection, "trigger", None) or {}).get("type") == "import_once"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SOURCE_CLASSIFICATION_REQUIRED",
                "message": "Classify and migrate legacy source records before Synchronize management. Import source IDs must not be reused as binding IDs.",
            },
        )
