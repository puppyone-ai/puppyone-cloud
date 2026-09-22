from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.config import settings
from src.exceptions import AppException, ErrorCode
from src.platform.billing.operations import BillingOperation
from src.platform.billing.seats import SeatBillingService, is_billable_human_role
from src.platform.entitlements.models import EntitlementSnapshot


class _Organizations:
    def __init__(self, count: int = 1) -> None:
        self.count = count

    def count_billable_members(self, org_id: str) -> int:
        assert org_id == "org-1"
        return self.count


class _Entitlements:
    def __init__(self, seats: int) -> None:
        self.seats = seats

    def purchased_seats(self, org_id: str) -> int:
        assert org_id == "org-1"
        return self.seats

    def get_snapshot(self, org_id: str) -> EntitlementSnapshot:
        return EntitlementSnapshot(org_id=org_id, seat_quantity=self.seats, source_revision=7)


class _UnavailableEntitlements(_Entitlements):
    def get_snapshot(self, org_id: str) -> EntitlementSnapshot:
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=503,
            message="snapshot unavailable",
        )

    def purchased_seats(self, org_id: str) -> int:
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=503,
            message="snapshot unavailable",
        )


class _Operations:
    def __init__(self, entitlements: _Entitlements | None = None) -> None:
        self.rows: dict[tuple[str, str], BillingOperation] = {}
        self.entitlements = entitlements or _Entitlements(seats=1)

    def get_by_key(self, org_id: str, key: str):
        return self.rows.get((org_id, key))

    def create_or_get(self, values):
        key = (values["org_id"], values["idempotency_key"])
        row = self.rows.get(key)
        if row is None:
            row = BillingOperation(
                id=f"op-{len(self.rows) + 1}",
                created_at=datetime.now(UTC),
                **values,
            )
            self.rows[key] = row
        return row

    def update(self, operation_id: str, values):
        key, row = next((key, row) for key, row in self.rows.items() if row.id == operation_id)
        row = row.model_copy(update=values)
        self.rows[key] = row
        return row

    def reserve_member_activation(self, **values):
        existing = next(
            (
                row
                for row in self.rows.values()
                if row.subject_user_id == values["subject_user_id"] and row.completed_at is None
            ),
            None,
        )
        current = 1
        target = current + 1
        # Mirrors the SQL boundary: authoritative capacity is read from the
        # persisted entitlement projection, never trusted from RPC arguments.
        confirmed = target <= self.entitlements.seats
        update = {
            "status": "confirmed" if confirmed else "awaiting_confirmation",
            "current_seat_quantity": current,
            "target_seat_quantity": target,
            "confirmed_revision": 7 if confirmed else None,
            "response_payload": {
                "requires_checkout": not confirmed,
            },
        }
        if existing is not None:
            return self.update(existing.id, update)
        return self.create_or_get(
            {
                "org_id": values["org_id"],
                "kind": "member_activation",
                "idempotency_key": values["idempotency_key"],
                "actor_user_id": values["actor_user_id"],
                "subject_user_id": values["subject_user_id"],
                "invitation_id": values["invitation_id"],
                "request_payload": {"role": values["role"]},
                **update,
            }
        )


def test_billable_role_policy_is_explicit() -> None:
    assert is_billable_human_role("owner")
    assert is_billable_human_role("member")
    assert not is_billable_human_role("viewer")


def test_required_mode_records_saga_and_denies_before_membership(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    entitlements = _Entitlements(seats=1)
    operations = _Operations(entitlements)
    service = SeatBillingService(
        _Organizations(count=1),
        entitlement_service=entitlements,
        operation_repository=operations,
    )

    with pytest.raises(AppException) as caught:
        service.ensure_member_activation(
            org_id="org-1",
            subject_user_id="user-2",
            role="member",
            actor_user_id="user-2",
            invitation_id="invite-1",
        )

    assert caught.value.status_code == 409
    assert caught.value.details["code"] == "seat_purchase_required"
    operation = next(iter(operations.rows.values()))
    assert operation.status == "awaiting_confirmation"
    assert operation.target_seat_quantity == 2


def test_exact_retry_finalizes_after_new_entitlement_revision(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    entitlements = _Entitlements(seats=1)
    operations = _Operations(entitlements)
    service = SeatBillingService(
        _Organizations(count=1),
        entitlement_service=entitlements,
        operation_repository=operations,
    )
    with pytest.raises(AppException):
        service.ensure_member_activation(
            org_id="org-1",
            subject_user_id="user-2",
            role="member",
            actor_user_id="user-2",
            invitation_id="invite-1",
        )

    entitlements.seats = 2
    operation = service.ensure_member_activation(
        org_id="org-1",
        subject_user_id="user-2",
        role="member",
        actor_user_id="user-2",
        invitation_id="invite-1",
    )
    assert operation is not None
    assert operation.status == "confirmed"
    assert operation.confirmed_revision == 7


def test_shadow_snapshot_outage_does_not_grant_a_fake_commercial_decision(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "shadow")
    service = SeatBillingService(
        _Organizations(count=1),
        entitlement_service=_UnavailableEntitlements(seats=0),
        operation_repository=_Operations(),
    )

    assert (
        service.ensure_member_activation(
            org_id="org-1",
            subject_user_id="user-2",
            role="member",
            actor_user_id="user-1",
        )
        is None
    )


def test_deactivation_is_never_blocked_by_entitlement_outage(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    service = SeatBillingService(
        _Organizations(count=1),
        entitlement_service=_UnavailableEntitlements(seats=0),
        operation_repository=_Operations(),
    )

    assert (
        service.record_member_deactivation(
            org_id="org-1",
            subject_user_id="user-2",
            actor_user_id="user-1",
            previous_role="member",
        )
        is None
    )


def test_repeated_deactivation_generations_do_not_reuse_an_old_outbox_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    operations = _Operations()
    service = SeatBillingService(
        _Organizations(count=1),
        entitlement_service=_Entitlements(seats=2),
        operation_repository=operations,
    )

    first = service.record_member_deactivation(
        org_id="org-1",
        subject_user_id="user-2",
        actor_user_id="user-1",
        previous_role="member",
    )
    second = service.record_member_deactivation(
        org_id="org-1",
        subject_user_id="user-2",
        actor_user_id="user-1",
        previous_role="member",
    )

    assert first is not None and second is not None
    assert first.idempotency_key != second.idempotency_key
