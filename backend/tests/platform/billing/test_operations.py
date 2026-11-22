from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.platform.billing.operations import BillingOperation


def _operation(
    *,
    kind: str,
    status: str,
    last_error: str | None = None,
    completed: bool = False,
) -> BillingOperation:
    return BillingOperation.model_validate(
        {
            "id": "operation-1",
            "org_id": "org-1",
            "kind": kind,
            "status": status,
            "idempotency_key": "operation-key",
            "last_error": last_error,
            "completed_at": datetime.now(UTC) if completed else None,
        }
    )


@pytest.mark.parametrize(
    ("kind", "status", "completed", "state", "terminal", "retryable", "action_required"),
    [
        ("checkout", "pending", False, "pending", False, True, False),
        ("member_activation", "quoted", False, "requires_action", False, False, True),
        ("checkout", "submitted", False, "processing", False, True, False),
        ("member_activation", "confirmed", False, "processing", False, True, False),
        ("entitlement_provision", "failed", False, "retryable_failed", False, True, False),
        ("member_activation", "failed", False, "failed", True, False, False),
        ("checkout", "confirmed", True, "succeeded", True, False, False),
        ("checkout", "canceled", False, "canceled", True, False, False),
    ],
)
def test_public_lifecycle_is_closed_and_kind_aware(
    kind: str,
    status: str,
    completed: bool,
    state: str,
    terminal: bool,
    retryable: bool,
    action_required: bool,
) -> None:
    view = _operation(
        kind=kind,
        status=status,
        last_error="safe_error_code",
        completed=completed,
    ).public_view()

    assert view.state == state
    assert view.terminal is terminal
    assert view.retryable is retryable
    assert view.action_required is action_required
    assert view.error_code == "safe_error_code"


def _applied(**quote) -> dict:
    return {
        "quote_id": "quote-1",
        "current_plan_id": "enterprise",
        "target_plan_id": "team",
        "current_seats": 2,
        "target_seats": 2,
        "effective": "now",
        **quote,
    }


@pytest.mark.parametrize(
    ("kind", "application", "state"),
    [
        # A downgrade scheduled for renewal publishes nothing now.
        ("plan_change", _applied(effective="next_period"), "succeeded"),
        (
            "seat_decrease",
            _applied(target_plan_id="enterprise", target_seats=1, effective="next_period"),
            "succeeded",
        ),
        # "Keep current plan" only withdraws the scheduled change.
        ("plan_change", _applied(target_plan_id="enterprise"), "succeeded"),
        # An upgrade waits for the entitlement it publishes.
        ("plan_change", _applied(target_plan_id="max"), "processing"),
        ("seat_increase", _applied(effective="next_period"), "processing"),
        ("plan_change", {"quote_id": "quote-1"}, "processing"),
    ],
)
def test_a_submitted_change_that_publishes_nothing_now_has_ended(
    kind: str, application: dict, state: str
) -> None:
    operation = _operation(kind=kind, status="submitted").model_copy(
        update={"response_payload": {"schema_version": "1.1", "application": application}}
    )

    view = operation.public_view()

    assert view.state == state
    assert view.terminal is (state == "succeeded")
