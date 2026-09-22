from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.config import settings
from src.exceptions import AppException, ErrorCode
from src.platform.billing.operations import BillingOperation, BillingOperationRepository
from src.platform.entitlements.service import EntitlementService

BILLABLE_HUMAN_ROLES = frozenset({"owner", "member"})
BILLABLE_PROJECT_ROLES = frozenset({"admin", "editor"})
logger = logging.getLogger(__name__)


def is_billable_human_role(role: str) -> bool:
    return role in BILLABLE_HUMAN_ROLES


def is_billable_project_role(role: str) -> bool:
    """Return whether the canonical Project role grants write/runtime capability."""

    return role in BILLABLE_PROJECT_ROLES


class SeatBillingService:
    """Product-side seat activation saga.

    Pending activations are durable operation rows, never memberships. This
    makes unpaid access structurally impossible even if a worker crashes.
    """

    def __init__(
        self,
        organization_repository: Any,
        *,
        entitlement_service: EntitlementService | None = None,
        operation_repository: BillingOperationRepository | None = None,
    ) -> None:
        self._organizations = organization_repository
        self._entitlements = entitlement_service or EntitlementService()
        self._operations = operation_repository

    @property
    def _operation_repo(self) -> BillingOperationRepository:
        if self._operations is None:
            self._operations = BillingOperationRepository()
        return self._operations

    def ensure_member_activation(
        self,
        *,
        org_id: str,
        subject_user_id: str,
        role: str,
        actor_user_id: str,
        invitation_id: str | None = None,
        grants_billable_capability: bool | None = None,
    ) -> BillingOperation | None:
        is_billable = (
            is_billable_human_role(role)
            if grants_billable_capability is None
            else grants_billable_capability
        )
        if not is_billable or settings.SEAT_BILLING_MODE == "disabled":
            return None
        try:
            snapshot = self._entitlements.get_snapshot(org_id)
        except AppException:
            if settings.SEAT_BILLING_MODE == "required":
                raise
            logger.warning(
                "seat_billing_shadow_snapshot_unavailable",
                extra={"org_id": org_id, "subject_user_id": subject_user_id},
            )
            return None
        purchased = max(0, int(snapshot.seat_quantity))
        if settings.SEAT_BILLING_MODE == "required":
            operation = self._operation_repo.reserve_member_activation(
                org_id=org_id,
                subject_user_id=subject_user_id,
                actor_user_id=actor_user_id,
                invitation_id=invitation_id,
                role=role,
                idempotency_key=f"member-activation:{subject_user_id}:{uuid4()}",
            )
            if operation.status == "confirmed":
                return operation
            raise AppException(
                code=ErrorCode.FORBIDDEN,
                status_code=409,
                message="A paid seat must be confirmed before granting billable access",
                details={
                    "code": "seat_purchase_required",
                    "operation_id": operation.id,
                    "org_id": org_id,
                    "purchased_seat_quantity": purchased,
                    "target_seat_quantity": operation.target_seat_quantity,
                    "retryable": True,
                },
            )

        current = self._organizations.count_billable_members(org_id)
        target = current + 1
        key = f"member-activation:{invitation_id or subject_user_id}:{target}"
        if target <= purchased:
            existing = self._operation_repo.get_by_key(org_id, key)
            if existing and existing.status != "confirmed":
                return self._operation_repo.update(
                    existing.id,
                    {
                        "status": "confirmed",
                        "confirmed_revision": self._entitlements.get_snapshot(
                            org_id
                        ).source_revision,
                        "completed_at": datetime.now(UTC).isoformat(),
                        "last_error": None,
                    },
                )
            return existing

        operation = self._operation_repo.create_or_get(
            {
                "org_id": org_id,
                "kind": "member_activation",
                "status": "awaiting_confirmation",
                "idempotency_key": key,
                "actor_user_id": actor_user_id,
                "subject_user_id": subject_user_id,
                "invitation_id": invitation_id,
                "current_seat_quantity": current,
                "target_seat_quantity": target,
                "request_payload": {
                    "schema_version": "1.0",
                    "role": role,
                    "purchased_seat_quantity": purchased,
                },
                "response_payload": {
                    "requires_checkout": True,
                },
            }
        )
        if settings.SEAT_BILLING_MODE == "shadow":
            self._operation_repo.update(
                operation.id,
                {
                    "response_payload": {
                        **operation.response_payload,
                        "shadow_would_deny": True,
                    }
                },
            )
            return operation
        raise AppException(
            code=ErrorCode.FORBIDDEN,
            status_code=409,
            message="A paid seat must be confirmed before granting billable access",
            details={
                "code": "seat_purchase_required",
                "operation_id": operation.id,
                "org_id": org_id,
                "purchased_seat_quantity": purchased,
                "target_seat_quantity": target,
                "retryable": True,
            },
        )

    def complete_member_activation(self, operation: BillingOperation | None) -> None:
        if operation is None or operation.status != "confirmed" or operation.completed_at:
            return
        self._operation_repo.update(
            operation.id,
            {
                "completed_at": datetime.now(UTC).isoformat(),
                "last_error": None,
            },
        )

    def record_member_deactivation(
        self,
        *,
        org_id: str,
        subject_user_id: str,
        actor_user_id: str,
        previous_role: str,
        was_billable: bool | None = None,
    ) -> BillingOperation | None:
        is_billable = (
            is_billable_human_role(previous_role) if was_billable is None else was_billable
        )
        if not is_billable or settings.SEAT_BILLING_MODE == "disabled":
            return None
        target = self._organizations.count_billable_members(org_id)
        try:
            purchased = self._entitlements.purchased_seats(org_id)
        except AppException:
            # Removing capability must remain possible during a control-plane
            # outage. A later seat/product reconciliation observes the lower
            # canonical member count and repairs any missed notification.
            logger.warning(
                "seat_deactivation_snapshot_unavailable",
                extra={"org_id": org_id, "subject_user_id": subject_user_id},
            )
            return None
        return self._operation_repo.create_or_get(
            {
                "org_id": org_id,
                "kind": "member_deactivation",
                "status": "pending",
                # A user can be deactivated, reactivated, and deactivated at
                # the same organization seat count. Each capability-removal
                # event needs a fresh outbox identity; target quantity alone
                # would collide with a completed operation from a prior cycle.
                "idempotency_key": f"member-deactivation:{subject_user_id}:{uuid4()}",
                "actor_user_id": actor_user_id,
                "subject_user_id": subject_user_id,
                "current_seat_quantity": purchased,
                "target_seat_quantity": target,
                "request_payload": {"schema_version": "1.0", "previous_role": previous_role},
            }
        )
