from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import src.platform.billing.router as billing_router
from src.config import settings
from src.exceptions import AppException
from src.platform.billing.gateway import BillingGatewayError
from src.platform.billing.models import CheckoutRequest, QuoteApplyRequest
from src.platform.billing.operations import BillingOperation
from src.platform.billing.router import (
    checkout,
    operation,
    operations,
    plan_change,
    seats_change,
)
from tests.platform.billing.support import _Gateway, _Operations, _Organizations

OWNER = SimpleNamespace(user_id="user-1", email="owner@example.com")


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "BILLING_UI_ENABLED", True)
    monkeypatch.setattr(settings, "BILLING_WRITES_ENABLED", True)


class _Space(_Gateway):
    """PuppyPay for one Space: its summary, and the quote any other call returns."""

    def __init__(self, quote: dict[str, Any], *, summary: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.quote = {"quote_id": "quote-1", "org_id": "org-1", **quote}
        self.summary = summary or {"plan_id": "team", "seat_quantity": 2, "source_revision": 4}

    async def request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("/summary"):
            return self.summary
        return self.quote


def seat_quote(current: int, target: int, **extra) -> dict[str, Any]:
    return {
        "current_plan_id": "team",
        "target_plan_id": "team",
        "current_seats": current,
        "target_seats": target,
        "application_mode": "seat_change",
        **extra,
    }


async def change_seats(gateway: _Gateway, operations: _Operations, monkeypatch, key="seats-1"):
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: operations)
    return await seats_change(
        "org-1",
        QuoteApplyRequest(quote_id="quote-1"),
        authorization="Bearer desktop-token",
        idempotency_key=key,
        current_user=OWNER,
        org_service=_Organizations("owner"),
        gateway=gateway,
    )


@pytest.mark.asyncio
async def test_adding_people_waits_for_the_entitlement_it_buys(enabled, monkeypatch) -> None:
    gateway = _Space(seat_quote(2, 3, effective="now"))
    stored = _Operations()

    result = await change_seats(gateway, stored, monkeypatch)

    assert gateway.calls[-1][1] == "/api/v1/billing/organizations/org-1/seats/change"
    assert stored.values["kind"] == "seat_increase"
    assert stored.values["baseline_source_revision"] == 4
    assert result["operation"]["state"] == "processing"


@pytest.mark.asyncio
async def test_fewer_people_at_renewal_ends_when_puppypay_accepts(enabled, monkeypatch) -> None:
    gateway = _Space(seat_quote(3, 2, effective="next_period"))
    stored = _Operations()

    result = await change_seats(gateway, stored, monkeypatch)

    assert stored.values["kind"] == "seat_decrease"
    assert result["operation"]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_a_seat_change_answered_with_a_plan_change_is_refused(enabled, monkeypatch) -> None:
    gateway = _Space(seat_quote(2, 3, application_mode="plan_change"))
    with pytest.raises(AppException) as caught:
        await change_seats(gateway, _Operations(), monkeypatch)
    assert caught.value.details["code"] == "billing_quote_application_mismatch"


@pytest.mark.asyncio
async def test_a_seat_quote_that_changes_the_plan_is_a_contract_violation(
    enabled, monkeypatch
) -> None:
    gateway = _Space(seat_quote(2, 3, target_plan_id="enterprise"))
    with pytest.raises(AppException) as caught:
        await change_seats(gateway, _Operations(), monkeypatch)
    assert (caught.value.status_code, caught.value.details["code"]) == (
        502,
        "billing_quote_contract_invalid",
    )


@pytest.mark.asyncio
async def test_a_plan_change_answered_with_a_seat_change_is_refused(enabled, monkeypatch) -> None:
    gateway = _Space(seat_quote(2, 3))
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: _Operations())
    with pytest.raises(AppException) as caught:
        await plan_change(
            "org-1",
            QuoteApplyRequest(quote_id="quote-1"),
            authorization="Bearer desktop-token",
            idempotency_key="plan-1",
            current_user=OWNER,
            org_service=_Organizations("owner"),
            gateway=gateway,
        )
    assert caught.value.details["code"] == "billing_quote_application_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "summary",
    [
        {"plan_id": "team", "seat_quantity": 2},
        {"plan_id": "team", "seat_quantity": 2, "source_revision": -1},
        {"plan_id": "team", "seat_quantity": 2, "source_revision": True},
    ],
)
async def test_a_summary_without_a_valid_revision_is_a_contract_violation(
    enabled, monkeypatch, summary
) -> None:
    gateway = _Space(seat_quote(2, 3), summary=summary)
    with pytest.raises(AppException) as caught:
        await change_seats(gateway, _Operations(), monkeypatch)
    assert caught.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quote",
    [
        {"quote": "not-an-object"},
        {"current_plan_id": "", "target_plan_id": "team", "current_seats": 2, "target_seats": 3},
        seat_quote(2, 0),
        seat_quote(2, 3) | {"org_id": "org-2"},
    ],
)
async def test_an_inconsistent_quote_from_puppypay_is_refused(enabled, monkeypatch, quote) -> None:
    gateway = _Space(quote)
    with pytest.raises(AppException) as caught:
        await change_seats(gateway, _Operations(), monkeypatch)
    assert caught.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "canceled"])
async def test_an_ended_operation_cannot_be_applied_again(enabled, monkeypatch, status) -> None:
    gateway = _Space(seat_quote(2, 3))
    stored = _Operations()
    stored.operation = BillingOperation(
        id="operation-1",
        org_id="org-1",
        kind="seat_increase",
        status=status,
        idempotency_key="seats-1",
        quote_id="quote-1",
        target_plan_id="team",
        current_seat_quantity=2,
        target_seat_quantity=3,
        request_payload={"application_mode": "seat_change"},
    )
    with pytest.raises(AppException) as caught:
        await change_seats(gateway, stored, monkeypatch)
    assert caught.value.status_code == 409
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_a_refusal_puppypay_will_not_retry_ends_the_operation(enabled, monkeypatch) -> None:
    class Refusing(_Space):
        async def request(self, method: str, path: str, **kwargs):
            if path.endswith("/seats/change"):
                self.calls.append((method, path, kwargs))
                raise BillingGatewayError(
                    409,
                    {
                        "error": {
                            "code": "quote_expired",
                            "message": "expired",
                            "retryable": False,
                        }
                    },
                )
            return await super().request(method, path, **kwargs)

    stored = _Operations()
    with pytest.raises(AppException):
        await change_seats(Refusing(seat_quote(2, 3)), stored, monkeypatch)
    assert stored.operation.status == "failed"
    assert stored.operation.last_error == "quote_expired"
    assert stored.operation.completed_at is not None


@pytest.mark.asyncio
async def test_changes_are_refused_while_billing_writes_are_off(enabled, monkeypatch) -> None:
    monkeypatch.setattr(settings, "BILLING_WRITES_ENABLED", False)
    gateway = _Space(seat_quote(2, 3))
    with pytest.raises(AppException) as caught:
        await change_seats(gateway, _Operations(), monkeypatch)
    assert caught.value.status_code == 503
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_a_checkout_needs_a_quote(enabled, monkeypatch) -> None:
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: _Operations())
    with pytest.raises(AppException) as caught:
        await checkout(
            "org-1",
            CheckoutRequest(plan_id="team", seat_quantity=1),
            authorization="Bearer desktop-token",
            idempotency_key="checkout-1",
            current_user=OWNER,
            org_service=_Organizations("owner"),
            gateway=_Gateway(),
        )
    assert (caught.value.status_code, caught.value.details["code"]) == (
        409,
        "billing_quote_required",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (
            {
                "checkout_id": "c1",
                "checkout_url": "https://checkout.example/1",
                "quote": seat_quote(1, 1),
            },
            "billing_checkout_contract_invalid",
        ),
        (
            {
                "checkout_url": "https://checkout.example/1",
                "quote": {
                    **seat_quote(1, 1),
                    "application_mode": "checkout",
                    "current_plan_id": "free",
                    "target_plan_id": "team",
                },
            },
            "billing_checkout_contract_invalid",
        ),
    ],
)
async def test_a_checkout_answer_that_breaks_the_contract_is_refused(
    enabled, monkeypatch, answer, code
) -> None:
    class Checkout(_Gateway):
        async def request(self, method: str, path: str, **kwargs):
            self.calls.append((method, path, kwargs))
            if path.endswith("/summary"):
                return {"plan_id": "free", "seat_quantity": 1, "source_revision": 1}
            if "/quotes/" in path:
                return {
                    "quote_id": "quote-1",
                    "org_id": "org-1",
                    "current_plan_id": "free",
                    "target_plan_id": "team",
                    "current_seats": 1,
                    "target_seats": 1,
                    "application_mode": "checkout",
                }
            return {
                **answer,
                "quote": {"quote_id": "quote-1", "org_id": "org-1", **answer["quote"]},
            }

    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: _Operations())
    with pytest.raises(AppException) as caught:
        await checkout(
            "org-1",
            CheckoutRequest(plan_id="team", seat_quantity=1, quote_id="quote-1"),
            authorization="Bearer desktop-token",
            idempotency_key="checkout-1",
            current_user=OWNER,
            org_service=_Organizations("owner"),
            gateway=Checkout(),
        )
    assert (caught.value.status_code, caught.value.details["code"]) == (502, code)


class _Listing(_Operations):
    def __init__(self, rows: list[BillingOperation]) -> None:
        super().__init__()
        self.rows = rows

    def list_for_org(self, org_id: str, *, limit: int = 100) -> list[BillingOperation]:
        return [row for row in self.rows if row.org_id == org_id]

    def get_by_id(self, org_id: str, operation_id: str) -> BillingOperation | None:
        return next(
            (row for row in self.rows if (row.org_id, row.id) == (org_id, operation_id)), None
        )


@pytest.mark.asyncio
async def test_the_owner_reads_operations_as_their_public_state(enabled, monkeypatch) -> None:
    rows = [
        BillingOperation(
            id="op-1", org_id="org-1", kind="checkout", status="submitted", idempotency_key="k1"
        ),
        BillingOperation(
            id="op-2",
            org_id="org-1",
            kind="plan_change",
            status="failed",
            idempotency_key="k2",
            last_error="quote_expired",
        ),
    ]
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: _Listing(rows))
    listed = await operations("org-1", current_user=OWNER, org_service=_Organizations("owner"))
    assert [(item["id"], item["state"]) for item in listed] == [
        ("op-1", "processing"),
        ("op-2", "failed"),
    ]
    one = await operation("org-1", "op-2", current_user=OWNER, org_service=_Organizations("owner"))
    assert one["error_code"] == "quote_expired"
    with pytest.raises(AppException) as caught:
        await operation("org-1", "op-9", current_user=OWNER, org_service=_Organizations("owner"))
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_only_the_owner_reads_operations(enabled, monkeypatch) -> None:
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: _Listing([]))
    with pytest.raises(AppException) as caught:
        await operations("org-1", current_user=OWNER, org_service=_Organizations("member"))
    assert caught.value.status_code == 403
