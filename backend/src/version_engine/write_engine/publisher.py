"""L5 publish boundary for version writes.

The engine builds an accepted version fact; this module owns the final
compare-and-swap publish, post-publish hook scheduling, and response shaping.
Keeping it here makes the L5 linearization point explicit without mixing it
into intent-specific merge flows.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from typing import Any

from src.config import settings
from src.exceptions import AppException, ErrorCode
from src.utils.logger import log_error
from src.version_engine.domain.intents import TransactionResult
from src.version_engine.write_engine.path_utils import normalize_path
from src.version_engine.write_engine.trace import trace_phase


async def publish_project_update(
    *,
    repo: Any,
    repo_manager: Any,
    project_id: str,
    old_root_hash: str,
    new_root_hash: str,
    commit_id: str,
    actor: str,
    message: str,
    op_type: str,
    audit_detail: dict,
    changes: list[dict],
    conflicts: Any,
    created_at_iso: str,
    cas_attempt: int,
    merged: bool,
    merged_changes: list[dict],
    source_channel: str = "",
    policy: str = "",
    base_commit_id: str = "",
    client_commit_id: str = "",
    proposed_tree_id: str = "",
    intent_type: str = "operation",
    scope_path: str = "",
    scope_hash: str = "",
    scope_head_commit_id: str = "",
    expected_scope_head_commit_id: str | None = None,
) -> TransactionResult | None:
    from src.platform.billing.storage import get_storage_quota_service

    storage_measurement = await get_storage_quota_service().check_publish(
        project_id=project_id,
        repo_manager=repo_manager,
        store=repo.store,
        old_root_hash=old_root_hash,
        new_root_hash=new_root_hash,
    )
    scope_norm = normalize_path(scope_path)
    accepted_scope_hash = scope_hash or new_root_hash
    audit = {
        "scope": scope_norm,
        "commit_id": commit_id,
        "root_hash": new_root_hash,
        "scope_hash": accepted_scope_hash,
        "cas_attempts": cas_attempt,
        "changes": len(changes),
        "merged": merged,
        "conflict_count": len(conflicts or []),
        "root_authoritative": True,
        **(audit_detail or {}),
    }
    if scope_norm:
        audit.pop("project_root_operation", None)
    else:
        audit["project_root_operation"] = True
    with trace_phase("db.publish_project_update.rpc", commit_id=commit_id[:12]):
        try:
            published = await asyncio.to_thread(
                repo.publish_project_update,
                old_root_hash=old_root_hash,
                new_root_hash=new_root_hash,
                scope_path=scope_norm,
                scope_hash=accepted_scope_hash,
                scope_head_commit_id=scope_head_commit_id,
                expected_scope_head_commit_id=expected_scope_head_commit_id,
                commit_id=commit_id,
                who=actor,
                message=message,
                changes=changes,
                conflicts=conflicts,
                created_at_iso=created_at_iso,
                audit_event_type=op_type,
                audit_agent_id=actor,
                audit_detail=audit,
                source_channel=source_channel,
                policy=policy,
                base_commit_id=base_commit_id,
                client_commit_id=client_commit_id,
                proposed_tree_id=proposed_tree_id,
                intent_type=intent_type,
                storage_measurement=(
                    {
                        "org_id": storage_measurement.org_id,
                        "old_value": storage_measurement.old_org_bytes,
                        "delta": storage_measurement.delta_bytes,
                        "limit": storage_measurement.limit_bytes,
                        "entitlement_source_revision": (
                            storage_measurement.entitlement_source_revision
                        ),
                        "enforce": settings.STORAGE_ENFORCEMENT_MODE == "required",
                    }
                    if storage_measurement is not None
                    else None
                ),
            )
        except RuntimeError as exc:
            error_text = str(exc)
            if "storage_quota_exceeded" in error_text:
                raise AppException(
                    code=ErrorCode.FORBIDDEN,
                    status_code=413,
                    message="Organization storage quota exceeded",
                    details={
                        "code": "storage_quota_exceeded",
                        "org_id": storage_measurement.org_id if storage_measurement else None,
                        "limit_bytes": (
                            storage_measurement.limit_bytes if storage_measurement else None
                        ),
                        "retryable": False,
                    },
                ) from exc
            entitlement_errors = {
                "storage_billing_entitlement_unavailable": "entitlement_snapshot_missing",
                "storage_billing_entitlement_invalid": "entitlement_snapshot_invalid",
                "storage_billing_entitlement_changed": "entitlement_snapshot_changed",
            }
            entitlement_code = next(
                (code for marker, code in entitlement_errors.items() if marker in error_text),
                None,
            )
            if entitlement_code is None:
                raise
            raise AppException(
                code=ErrorCode.INTERNAL_SERVER_ERROR,
                status_code=503,
                message="Storage entitlement snapshot is temporarily unavailable",
                details={
                    "code": entitlement_code,
                    "org_id": storage_measurement.org_id if storage_measurement else None,
                    "retryable": True,
                },
            ) from exc
    if isinstance(published, tuple):
        published, _txn_id = published
    else:
        published, _txn_id = bool(published), None
    if not published:
        return None

    push_result = {
        "status": "ok",
        "commit_id": commit_id,
        "root": new_root_hash,
        "old_root": old_root_hash,
        "scope_path": scope_norm,
        "scope_hash": accepted_scope_hash,
        "merged": merged,
        "conflicts": len(conflicts or []),
    }
    try:
        from src.version_engine.derived.hooks import schedule_post_project_update_hook

        with trace_phase("hook.schedule_project_update", commit_id=commit_id[:12]):
            schedule_post_project_update_hook(project_id, repo_manager, push_result)
    except Exception as e:
        log_error(
            f"[version_engine][{op_type}:project] projection hook failed "
            f"(commit landed but derived views may lag): {e}",
        )

    return _transaction_result(
        repo=repo,
        commit_id=commit_id,
        root_hash=accepted_scope_hash,
        merged=merged,
        conflicts=conflicts,
        changes=changes,
        merged_changes=merged_changes,
    )


def _transaction_result(
    *,
    repo: Any,
    commit_id: str,
    root_hash: str,
    merged: bool,
    conflicts: Any,
    changes: list[dict],
    merged_changes: list[dict],
) -> TransactionResult:
    commit_object = ""
    with suppress(Exception):
        commit_object = base64.b64encode(repo.store.get_loose(commit_id)).decode()

    return TransactionResult(
        commit_id=commit_id,
        status="ok",
        merged=merged,
        conflicts=len(conflicts or []),
        paths=[c["path"] for c in changes],
        changes=changes,
        new_scope_hash=root_hash,
        is_noop=False,
        merged_changes=merged_changes,
        commit_object=commit_object,
    )
