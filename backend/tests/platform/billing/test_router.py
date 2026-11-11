from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.platform.billing.router as billing_router
from src.config import settings
from src.exceptions import AppException, ForbiddenException
from src.platform.billing.gateway import BillingGatewayError
from src.platform.billing.models import (
    AddOnChangeRequest,
    CancellationRequest,
    CheckoutRequest,
    PlanQuoteRequest,
    QuoteApplyRequest,
    RuntimeOverageRequest,
    SeatQuoteRequest,
    TopUpRequest,
)
from src.platform.billing.operations import BillingOperation
from src.platform.billing.router import (
    _seat_operation,
    _translate,
    _usage_percent,
    catalog,
    checkout,
    plan_change,
    plan_quote,
    seat_quote,
    summary,
)


class _Organizations:
    def __init__(self, role: str, org_id: str = "org-1") -> None:
        self.role = role
        self.org_id = org_id

    def get_my_role(self, org_id: str, user_id: str) -> str:
        assert org_id == self.org_id
        assert user_id == "user-1"
        return self.role


class _Gateway:
    def __init__(self, response: dict | None = None, timeline: list[str] | None = None) -> None:
        self.response = response or {"ok": True}
        self.calls: list[tuple[str, str, dict]] = []
        self.timeline = timeline

    async def request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.timeline is not None:
            self.timeline.append(f"gateway:{path.rsplit('/', 1)[-1]}")
        return self.response


class _CheckoutGateway(_Gateway):
    async def request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.timeline is not None:
            self.timeline.append(f"gateway:{path.rsplit('/', 1)[-1]}")
        if path.endswith("/summary"):
            return {
                "plan_id": "free",
                "seat_quantity": 1,
                "source_revision": 7,
            }
        return {
            "checkout_id": "checkout-1",
            "checkout_url": "https://checkout.example/session",
            "quote": {
                "quote_id": "quote-1",
                "org_id": "org-1",
                "current_plan_id": "free",
                "target_plan_id": "plus",
                "current_seats": 1,
                "target_seats": 2,
                "application_mode": "checkout",
            },
        }


class _PlanChangeGateway(_Gateway):
    async def request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("/summary"):
            return {
                "plan_id": "free",
                "seat_quantity": 1,
                "source_revision": 99,
            }
        return {
            "quote_id": "quote-1",
            "org_id": "org-1",
            "current_plan_id": "free",
            "target_plan_id": "plus",
            "current_seats": 1,
            "target_seats": 2,
            "application_mode": "plan_change",
        }


class _Operations:
    def __init__(self, timeline: list[str] | None = None) -> None:
        self.values = None
        self.operation: BillingOperation | None = None
        self.timeline = timeline
        self.reject_nonterminal_update = False

    def get_by_key(self, org_id: str, key: str) -> BillingOperation | None:
        if self.timeline is not None:
            self.timeline.append("repository:get")
        if self.operation is None:
            return None
        return (
            self.operation
            if (self.operation.org_id, self.operation.idempotency_key)
            == (
                org_id,
                key,
            )
            else None
        )

    def get_by_quote_id(self, org_id: str, quote_id: str) -> BillingOperation | None:
        if self.operation is None:
            return None
        return (
            self.operation
            if (self.operation.org_id, self.operation.quote_id) == (org_id, quote_id)
            else None
        )

    def get_by_id(self, org_id: str, operation_id: str) -> BillingOperation | None:
        if self.operation is None:
            return None
        return (
            self.operation
            if (self.operation.org_id, self.operation.id) == (org_id, operation_id)
            else None
        )

    def create_commercial_operation(self, **values) -> BillingOperation:
        if self.timeline is not None:
            self.timeline.append("repository:create-intent")
        self.values = values
        self.operation = BillingOperation(
            id="operation-1",
            org_id=values["org_id"],
            kind=values["kind"],
            status="pending",
            idempotency_key=values["idempotency_key"],
            actor_user_id=values["actor_user_id"],
            target_plan_id=values["target_plan_id"],
            current_seat_quantity=values["current_seat_quantity"],
            target_seat_quantity=values["target_seat_quantity"],
            quote_id=values["quote_id"],
            baseline_source_revision=values["baseline_source_revision"],
            request_payload={"application_mode": values["application_mode"]},
        )
        return self.operation

    def update_nonterminal(self, operation_id: str, values: dict) -> BillingOperation | None:
        assert self.operation is not None and operation_id == self.operation.id
        if self.reject_nonterminal_update:
            return None
        if self.timeline is not None:
            self.timeline.append("repository:submit")
        self.operation = self.operation.model_copy(update=values)
        return self.operation

    def reconcile_from_entitlement(self, *, org_id: str, operation_id: str) -> BillingOperation:
        assert self.operation is not None
        assert (org_id, operation_id) == (self.operation.org_id, self.operation.id)
        if self.timeline is not None:
            self.timeline.append("repository:reconcile")
        return self.operation


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "BILLING_UI_ENABLED", True)
    monkeypatch.setattr(settings, "BILLING_WRITES_ENABLED", True)


@pytest.mark.asyncio
@pytest.mark.parametrize("allowlist", ["", "org-10"])
@pytest.mark.parametrize(
    "name,payload,extra",
    [
        ("plan_quote", PlanQuoteRequest(target_plan_id="plus", seat_quantity=1), {}),
        ("seat_quote", SeatQuoteRequest(seat_quantity=1), {}),
        ("checkout", CheckoutRequest(plan_id="plus", quote_id="old-quote"), {}),
        ("plan_change", QuoteApplyRequest(quote_id="old-quote"), {}),
        ("seats_change", QuoteApplyRequest(quote_id="old-quote"), {}),
        ("cancel", CancellationRequest(cancel_at_period_end=False), {}),
        ("portal", None, {}),
        ("runtime_top_up", TopUpRequest(pack_id="ru_1000"), {}),
        ("runtime_overage", RuntimeOverageRequest(enabled=True, monthly_limit_cents=100), {}),
        ("add_on", AddOnChangeRequest(quantity=1), {"add_on_id": "extra-hours"}),
    ],
)
async def test_all_purchase_routes_reject_outside_rollout_before_upstream(
    enabled,
    monkeypatch,
    allowlist,
    name,
    payload,
    extra,
):
    monkeypatch.setattr(settings, "BILLING_PURCHASE_ORG_IDS", allowlist)
    gateway = _Gateway()
    extra = dict(extra)
    args = ("org-1",) if payload is None else ("org-1", payload)
    if name == "add_on":
        args = ("org-1", extra.pop("add_on_id"), payload)
    with pytest.raises(AppException) as error:
        await getattr(billing_router, name)(
            *args,
            **extra,
            authorization="Bearer desktop-token",
            idempotency_key="purchase",
            current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
            org_service=_Organizations("owner"),
            gateway=gateway,
        )
    assert error.value.status_code == 503
    assert error.value.details["code"] == "billing_rollout_not_enabled"
    assert gateway.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "member"])
@pytest.mark.parametrize(
    "name,payload,extra",
    [
        ("cancel", CancellationRequest(cancel_at_period_end=True), {}),
        ("add_on", AddOnChangeRequest(quantity=0), {"add_on_id": "extra-hours"}),
        ("runtime_overage", RuntimeOverageRequest(enabled=False, monthly_limit_cents=0), {}),
    ],
)
async def test_stopping_charges_preserves_owner_checks_when_purchases_are_closed(
    enabled,
    monkeypatch,
    role,
    name,
    payload,
    extra,
):
    monkeypatch.setattr(settings, "BILLING_PURCHASE_ORG_IDS", "")
    monkeypatch.setattr(settings, "BILLING_WRITES_ENABLED", False)
    gateway = _Gateway()
    extra = dict(extra)
    args = ("org-1", payload)
    if name == "add_on":
        args = ("org-1", extra.pop("add_on_id"), payload)

    async def call():
        return await getattr(billing_router, name)(
            *args,
            **extra,
            authorization="Bearer desktop-token",
            idempotency_key="stop",
            current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
            org_service=_Organizations(role),
            gateway=gateway,
        )

    if role == "owner":
        assert await call() == {"ok": True}
        assert len(gateway.calls) == 1
    else:
        with pytest.raises(ForbiddenException):
            await call()
        assert gateway.calls == []


@pytest.mark.asyncio
async def test_rollout_allows_selected_owner_quote_and_preserves_reads(enabled, monkeypatch):
    monkeypatch.setattr(settings, "BILLING_PURCHASE_ORG_IDS", "org-1")
    gateway = _Gateway()
    kwargs = dict(
        authorization="Bearer desktop-token",
        current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
        org_service=_Organizations("owner"),
        gateway=gateway,
    )
    await plan_quote(
        "org-1",
        PlanQuoteRequest(target_plan_id="plus", seat_quantity=1),
        idempotency_key="quote",
        **kwargs,
    )
    monkeypatch.setattr(settings, "BILLING_PURCHASE_ORG_IDS", "")
    await summary("org-1", **kwargs)
    await catalog(gateway=gateway)
    assert [row[0] for row in gateway.calls] == ["POST", "GET", "GET"]


@pytest.mark.asyncio
async def test_catalog_is_fail_closed_when_ui_flag_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "BILLING_UI_ENABLED", False)
    gateway = _Gateway()
    with pytest.raises(AppException) as caught:
        await catalog(gateway=gateway)
    assert caught.value.status_code == 404
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_billing_reads_require_owner_before_calling_puppypay(enabled) -> None:
    gateway = _Gateway()
    with pytest.raises(ForbiddenException):
        await summary(
            "org-1",
            authorization="Bearer desktop-token",
            current_user=SimpleNamespace(user_id="user-1", email="member@example.com"),
            org_service=_Organizations("member"),
            gateway=gateway,
        )
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_checkout_requires_idempotency_and_uses_service_principal(
    enabled,
    monkeypatch,
) -> None:
    timeline: list[str] = []
    gateway = _CheckoutGateway(timeline=timeline)
    operations = _Operations(timeline)
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: operations)
    with pytest.raises(AppException) as missing:
        await checkout(
            "org-1",
            CheckoutRequest(plan_id="plus", seat_quantity=2, quote_id="quote-1"),
            authorization="Bearer desktop-token",
            idempotency_key=None,
            current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
            org_service=_Organizations("owner"),
            gateway=gateway,
        )
    assert missing.value.status_code == 400
    assert gateway.calls == []

    result = await checkout(
        "org-1",
        CheckoutRequest(plan_id="plus", seat_quantity=2, quote_id="quote-1"),
        authorization="Bearer desktop-token",
        idempotency_key="desktop:checkout:1",
        current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
        org_service=_Organizations("owner"),
        gateway=gateway,
    )
    _, _, kwargs = gateway.calls[-1]
    assert "authorization" not in kwargs
    assert kwargs["actor_user_id"] == "user-1"
    assert kwargs["actor_email"] == "owner@example.com"
    assert kwargs["idempotency_key"] == "desktop:checkout:1"
    assert result["operation"] == {
        "id": "operation-1",
        "org_id": "org-1",
        "kind": "checkout",
        "state": "processing",
        "terminal": False,
        "retryable": True,
        "action_required": False,
        "target_plan_id": "plus",
        "current_seat_quantity": 1,
        "target_seat_quantity": 2,
        "quote_id": "quote-1",
        "confirmed_revision": None,
        "error_code": None,
        "created_at": None,
        "updated_at": None,
        "completed_at": None,
    }
    assert operations.values == {
        "org_id": "org-1",
        "kind": "checkout",
        "idempotency_key": "desktop:checkout:1",
        "actor_user_id": "user-1",
        "quote_id": "quote-1",
        "application_mode": "checkout",
        "target_plan_id": "plus",
        "current_seat_quantity": 1,
        "target_seat_quantity": 2,
        "baseline_source_revision": 7,
        "response_payload": {
            "schema_version": "1.1",
            "quote": {
                "quote_id": "quote-1",
                "org_id": "org-1",
                "current_plan_id": "free",
                "target_plan_id": "plus",
                "current_seats": 1,
                "target_seats": 2,
                "application_mode": "checkout",
            },
        },
    }
    assert operations.operation is not None
    assert operations.operation.response_payload == {
        "schema_version": "1.1",
        "checkout_id": "checkout-1",
    }
    assert timeline.index("repository:create-intent") < timeline.index("gateway:checkout")


@pytest.mark.asyncio
async def test_plan_change_retry_reuses_the_pre_effect_baseline(enabled, monkeypatch) -> None:
    gateway = _PlanChangeGateway()
    operations = _Operations()
    operations.operation = BillingOperation(
        id="operation-1",
        org_id="org-1",
        kind="plan_change",
        status="pending",
        idempotency_key="desktop:plan-change:1",
        actor_user_id="user-1",
        target_plan_id="plus",
        current_seat_quantity=1,
        target_seat_quantity=2,
        quote_id="quote-1",
        baseline_source_revision=7,
        request_payload={"application_mode": "plan_change"},
    )
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: operations)

    result = await plan_change(
        "org-1",
        QuoteApplyRequest(
            quote_id="quote-1",
        ),
        authorization="Bearer desktop-token",
        idempotency_key="desktop:plan-change:1",
        current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
        org_service=_Organizations("owner"),
        gateway=gateway,
    )

    assert [path for _, path, _ in gateway.calls] == [
        "/api/v1/billing/organizations/org-1/plan/change"
    ]
    assert gateway.calls[0][2]["body"] == {"quote_id": "quote-1"}
    assert operations.operation is not None
    assert operations.operation.baseline_source_revision == 7
    assert result["operation"]["state"] == "processing"


@pytest.mark.asyncio
async def test_checkout_rejects_a_second_operation_for_the_same_quote(
    enabled,
    monkeypatch,
) -> None:
    gateway = _CheckoutGateway()
    operations = _Operations()
    operations.operation = BillingOperation(
        id="operation-existing",
        org_id="org-1",
        kind="checkout",
        status="pending",
        idempotency_key="desktop:checkout:original",
        target_plan_id="plus",
        current_seat_quantity=1,
        target_seat_quantity=2,
        quote_id="quote-1",
        baseline_source_revision=7,
        request_payload={"application_mode": "checkout"},
    )
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: operations)

    with pytest.raises(AppException) as caught:
        await checkout(
            "org-1",
            CheckoutRequest(plan_id="plus", seat_quantity=2, quote_id="quote-1"),
            authorization="Bearer desktop-token",
            idempotency_key="desktop:checkout:different",
            current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
            org_service=_Organizations("owner"),
            gateway=gateway,
        )

    assert caught.value.details["code"] == "billing_quote_already_linked"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_checkout_reuses_a_quote_linked_membership_operation(
    enabled,
    monkeypatch,
) -> None:
    gateway = _CheckoutGateway()
    operations = _Operations()
    operations.operation = BillingOperation(
        id="member-operation",
        org_id="org-1",
        kind="member_activation",
        status="quoted",
        idempotency_key="member-activation:user-2",
        target_seat_quantity=2,
        quote_id="quote-1",
    )
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: operations)

    result = await checkout(
        "org-1",
        CheckoutRequest(
            plan_id="plus",
            seat_quantity=2,
            quote_id="quote-1",
            operation_id="member-operation",
        ),
        authorization="Bearer desktop-token",
        idempotency_key="desktop:checkout:member",
        current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
        org_service=_Organizations("owner"),
        gateway=gateway,
    )

    assert operations.values is None
    assert operations.operation is not None
    assert operations.operation.kind == "member_activation"
    assert operations.operation.request_payload["application_mode"] == "checkout"
    assert result["operation"]["id"] == "member-operation"
    assert result["operation"]["state"] == "processing"
    assert gateway.calls[-1][2]["body"] == {
        "plan_id": "plus",
        "seat_quantity": 2,
        "quote_id": "quote-1",
    }
    assert gateway.calls[-1][2]["idempotency_key"] == "member-activation:user-2"


@pytest.mark.asyncio
async def test_seat_quote_cannot_regress_an_operation_that_finishes_during_upstream_call(
    enabled,
    monkeypatch,
) -> None:
    gateway = _Gateway(
        {
            "quote_id": "quote-1",
            "org_id": "org-1",
            "current_plan_id": "plus",
            "target_plan_id": "plus",
            "current_seats": 1,
            "target_seats": 2,
            "application_mode": "seat_change",
        }
    )
    operations = _Operations()
    operations.operation = BillingOperation(
        id="member-operation",
        org_id="org-1",
        kind="member_activation",
        status="pending",
        idempotency_key="member-activation:user-2",
        target_seat_quantity=2,
    )
    operations.reject_nonterminal_update = True
    monkeypatch.setattr(billing_router, "BillingOperationRepository", lambda: operations)

    with pytest.raises(AppException) as caught:
        await seat_quote(
            "org-1",
            SeatQuoteRequest(seat_quantity=2, operation_id="member-operation"),
            authorization="Bearer desktop-token",
            idempotency_key="desktop:seat-quote:member",
            current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
            org_service=_Organizations("owner"),
            gateway=gateway,
        )

    assert caught.value.details["code"] == "billing_operation_terminal"
    assert operations.operation.status == "pending"


@pytest.mark.asyncio
async def test_quote_requires_and_forwards_idempotency(enabled) -> None:
    gateway = _Gateway({"quote_id": "quote-1"})
    arguments = {
        "org_id": "org-1",
        "payload": PlanQuoteRequest(target_plan_id="plus", seat_quantity=2),
        "authorization": "Bearer desktop-token",
        "current_user": SimpleNamespace(user_id="user-1", email="owner@example.com"),
        "org_service": _Organizations("owner"),
        "gateway": gateway,
    }

    with pytest.raises(AppException) as missing:
        await plan_quote(**arguments, idempotency_key=None)
    assert missing.value.status_code == 400
    assert gateway.calls == []

    await plan_quote(**arguments, idempotency_key="desktop:plan-quote:1")
    assert gateway.calls[0][2]["idempotency_key"] == "desktop:plan-quote:1"


@pytest.mark.asyncio
async def test_billing_proxy_encodes_organization_path_segment(enabled) -> None:
    gateway = _Gateway({"org_id": "org/1"})

    await summary(
        "org/1",
        authorization="Bearer desktop-token",
        current_user=SimpleNamespace(user_id="user-1", email="owner@example.com"),
        org_service=_Organizations("owner", "org/1"),
        gateway=gateway,
    )

    assert gateway.calls[0][1] == "/api/v1/billing/organizations/org%2F1/summary"


def test_billing_proxy_does_not_forward_private_upstream_details() -> None:
    translated = _translate(
        BillingGatewayError(
            409,
            {
                "error": {
                    "code": "quote_conflict",
                    "message": "Quote conflict",
                    "details": {
                        "minimum": 1,
                        "provider_subscription_id": "sub_private",
                    },
                }
            },
        )
    )

    assert translated.details["upstream_details"] == {"minimum": 1}


def test_usage_percent_has_explicit_zero_quota_semantics() -> None:
    assert _usage_percent(0, 0) == 0
    assert _usage_percent(1, 0) == 100
    assert _usage_percent(50, 100) == 50
    assert _usage_percent(1, None) is None


@pytest.mark.parametrize("terminal_status", ["confirmed", "failed", "canceled"])
def test_linked_seat_operation_cannot_regress_from_a_terminal_state(
    terminal_status: str,
) -> None:
    item = BillingOperation(
        id="operation-1",
        org_id="org-1",
        kind="member_activation",
        status=terminal_status,
        idempotency_key="member-activation:user-2",
        target_seat_quantity=2,
        quote_id="quote-1",
    )
    repository = SimpleNamespace(get_by_id=lambda org_id, operation_id: item)

    with pytest.raises(AppException) as caught:
        _seat_operation(
            repository=repository,
            org_id="org-1",
            operation_id="operation-1",
            target_seat_quantity=2,
            quote_id="quote-1",
        )

    assert caught.value.details["code"] == "billing_operation_terminal"


@pytest.mark.asyncio
async def test_add_on_change_is_an_owner_only_idempotent_put(enabled) -> None:
    from src.platform.billing.models import AddOnChangeRequest
    from src.platform.billing.router import add_on

    gateway = _Gateway({"outcome": "checkout", "checkout_url": "https://checkout.example/1"})
    arguments = {
        "org_id": "org-1",
        "add_on_id": "agent_hours_100",
        "payload": AddOnChangeRequest(quantity=2),
        "authorization": "Bearer desktop-token",
        "current_user": SimpleNamespace(user_id="user-1", email="owner@example.com"),
        "gateway": gateway,
    }
    with pytest.raises(ForbiddenException):
        await add_on(**arguments, org_service=_Organizations("member"), idempotency_key="k-1")
    with pytest.raises(AppException):
        await add_on(**arguments, org_service=_Organizations("owner"), idempotency_key=None)
    assert gateway.calls == []

    result = await add_on(**arguments, org_service=_Organizations("owner"), idempotency_key="k-1")
    method, path, kwargs = gateway.calls[0]
    assert (method, path) == (
        "PUT",
        "/api/v1/billing/organizations/org-1/add-ons/agent_hours_100",
    )
    assert kwargs["body"] == {"quantity": 2}
    assert kwargs["idempotency_key"] == "k-1"
    assert result["checkout_url"] == "https://checkout.example/1"
