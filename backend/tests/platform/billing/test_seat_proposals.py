from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.config import settings
from src.platform.billing.operations import BillingOperation
from src.platform.billing.seat_proposals import SeatProposalService


def _operation(*, kind: str = "member_activation") -> BillingOperation:
    return BillingOperation(
        id="operation-1",
        org_id="org-1",
        kind=kind,
        status="awaiting_confirmation" if kind == "member_activation" else "pending",
        idempotency_key="member-event-1",
        target_seat_quantity=15,
        attempts=1,
        updated_at=datetime(2026, 7, 14, tzinfo=UTC),
        response_payload={"requires_checkout": True},
    )


class _Operations:
    def __init__(self, operation: BillingOperation) -> None:
        self.operation = operation
        self.updates: list[dict] = []

    def claim_seat_proposals(self, *, limit: int, lease_seconds: int):
        assert limit == 25
        assert lease_seconds == 60
        return [self.operation]

    def update_claimed_seat_proposal(self, operation: BillingOperation, values: dict):
        assert operation == self.operation
        self.updates.append(values)
        self.operation = operation.model_copy(update=values)
        return self.operation


class _Organizations:
    def get_owner_user_id(self, org_id: str) -> str:
        assert org_id == "org-1"
        return "owner-1"


class _Gateway:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.error:
            raise self.error
        return {"quote_id": "quote-1", "requires_confirmation": True}


@pytest.mark.asyncio
async def test_worker_claims_and_submits_stable_owner_authorized_proposal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    monkeypatch.setattr(settings, "SEAT_PROPOSAL_LEASE_SECONDS", 60)
    operations = _Operations(_operation())
    gateway = _Gateway()
    service = SeatProposalService(
        gateway=gateway,
        operation_repository=operations,
        organization_repository=_Organizations(),
    )

    assert await service.recover_once(limit=25) == {
        "claimed": 1,
        "quoted": 1,
        "failed": 0,
    }
    assert gateway.calls == [
        (
            "POST",
            "/internal/v1/billing/seat-proposals",
            {
                "idempotency_key": "seat-proposal:operation-1",
                "body": {
                    "org_id": "org-1",
                    "proposed_seat_quantity": 15,
                    "requested_by_user_id": "owner-1",
                    "membership_event_id": "operation-1",
                },
            },
        )
    ]
    assert operations.operation.status == "quoted"
    assert operations.operation.quote_id == "quote-1"


@pytest.mark.asyncio
async def test_worker_retries_without_leaking_provider_error_or_granting_access(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    monkeypatch.setattr(settings, "SEAT_PROPOSAL_LEASE_SECONDS", 60)
    operations = _Operations(_operation(kind="member_deactivation"))
    service = SeatProposalService(
        gateway=_Gateway(error=RuntimeError("private provider body")),
        operation_repository=operations,
        organization_repository=_Organizations(),
    )

    assert await service.recover_once(limit=25) == {
        "claimed": 1,
        "quoted": 0,
        "failed": 1,
    }
    assert operations.operation.status == "pending"
    assert operations.operation.last_error == "RuntimeError"
    assert "private provider body" not in str(operations.updates)
    assert operations.operation.completed_at is None
