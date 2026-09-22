from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header

from src.config import settings
from src.exceptions import AppException, ErrorCode, ForbiddenException
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.billing.gateway import (
    BillingGatewayError,
    PuppyPayGateway,
    get_billing_gateway,
)
from src.platform.billing.models import (
    CancellationRequest,
    CheckoutRequest,
    PlanQuoteRequest,
    QuoteApplyRequest,
    RuntimeOverageRequest,
    SeatQuoteRequest,
    TopUpRequest,
)
from src.platform.billing.operations import BillingOperation, BillingOperationRepository
from src.platform.billing.storage import StorageUsageRepository
from src.platform.entitlements.service import EntitlementService
from src.platform.organization.dependencies import get_org_service
from src.platform.organization.service import OrganizationService

router = APIRouter(prefix="/billing", tags=["billing"])
_SAFE_UPSTREAM_DETAIL_KEYS = {
    "available_units",
    "maximum",
    "maximum_limit_cents",
    "minimum",
    "minimum_limit_cents",
    "plan_id",
    "requested_units",
    "retention_minimum",
    "retry_after_seconds",
    "status",
}


def _require_ui() -> None:
    if not settings.BILLING_UI_ENABLED:
        raise AppException(
            code=ErrorCode.NOT_FOUND,
            status_code=404,
            message="Billing is not enabled",
        )


def _require_writes() -> None:
    _require_ui()
    if not settings.BILLING_WRITES_ENABLED:
        raise AppException(
            code=ErrorCode.FORBIDDEN,
            status_code=503,
            message="Billing changes are temporarily disabled",
            details={"code": "billing_writes_disabled"},
        )


async def _require_owner(
    org_service: OrganizationService,
    org_id: str,
    user_id: str,
) -> None:
    role = await asyncio.to_thread(org_service.get_my_role, org_id, user_id)
    if role != "owner":
        raise ForbiddenException("Organization owner role is required for billing")


def _idempotency_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key or len(key) > 255:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=400,
            message="Idempotency-Key is required and must not exceed 255 characters",
        )
    return key


def _translate(error: BillingGatewayError) -> AppException:
    upstream = error.payload.get("error") if isinstance(error.payload, dict) else None
    upstream = upstream if isinstance(upstream, dict) else {}
    raw_details = upstream.get("details")
    raw_details = raw_details if isinstance(raw_details, dict) else {}
    safe_details = {
        key: value for key, value in raw_details.items() if key in _SAFE_UPSTREAM_DETAIL_KEYS
    }
    return AppException(
        code=ErrorCode.BAD_REQUEST if error.status_code < 500 else ErrorCode.INTERNAL_SERVER_ERROR,
        status_code=error.status_code,
        message=str(upstream.get("message") or "Billing request failed"),
        details={
            "code": str(upstream.get("code") or "billing_request_failed"),
            "retryable": bool(upstream.get("retryable", error.status_code >= 500)),
            "upstream_details": safe_details,
        },
    )


def _usage_percent(value: int, limit: int | None) -> int | None:
    if limit is None:
        return None
    if limit == 0:
        return 0 if value == 0 else 100
    return min(100, int(value * 100 / limit))


async def _call(gateway: PuppyPayGateway, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        return await gateway.request(method, path, **kwargs)
    except BillingGatewayError as exc:
        raise _translate(exc) from exc


@router.get("/catalog")
async def catalog(gateway: PuppyPayGateway = Depends(get_billing_gateway)) -> dict[str, Any]:
    _require_ui()
    return await _call(gateway, "GET", "/api/v1/billing/catalog")


async def _authorized_org_call(
    *,
    gateway: PuppyPayGateway,
    org_service: OrganizationService,
    current_user: CurrentUser,
    authorization: str,
    org_id: str,
    method: str,
    suffix: str,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    write: bool = False,
) -> dict[str, Any]:
    _require_writes() if write else _require_ui()
    await _require_owner(org_service, org_id, current_user.user_id)
    # The Desktop bearer token terminates at PuppyOne. PuppyPay receives a
    # service-authenticated acting principal and independently rechecks billing
    # access against PuppyOne; no end-user credential crosses the trust boundary.
    del authorization
    return await _call(
        gateway,
        method,
        f"/api/v1/billing/organizations/{quote(org_id, safe='')}/{suffix}",
        actor_user_id=current_user.user_id,
        actor_email=current_user.email,
        idempotency_key=idempotency_key,
        body=body,
    )


@router.get("/organizations/{org_id}/summary")
async def summary(
    org_id: str,
    authorization: Annotated[str, Header(alias="Authorization")],
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    return await _authorized_org_call(
        gateway=gateway,
        org_service=org_service,
        current_user=current_user,
        authorization=authorization,
        org_id=org_id,
        method="GET",
        suffix="summary",
    )


@router.get("/organizations/{org_id}/usage")
async def usage(
    org_id: str,
    authorization: Annotated[str, Header(alias="Authorization")],
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    runtime = await _authorized_org_call(
        gateway=gateway,
        org_service=org_service,
        current_user=current_user,
        authorization=authorization,
        org_id=org_id,
        method="GET",
        suffix="usage",
    )
    storage, raw_limit = await asyncio.gather(
        asyncio.to_thread(StorageUsageRepository().get, org_id),
        asyncio.to_thread(EntitlementService().limit_value, org_id, "storage.max_bytes"),
    )
    limit_bytes = int(raw_limit) if raw_limit is not None else None
    percent = _usage_percent(storage.value, limit_bytes)
    return {
        "runtime": runtime,
        "storage": {
            "logical_bytes": storage.value,
            "limit_bytes": limit_bytes,
            "percent": percent,
            "threshold_percent": storage.threshold_percent,
            "version": storage.version,
        },
    }


@router.get("/organizations/{org_id}/operations")
async def operations(
    org_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
) -> list[dict[str, Any]]:
    _require_ui()
    await _require_owner(org_service, org_id, current_user.user_id)
    items = await asyncio.to_thread(BillingOperationRepository().list_for_org, org_id)
    return [item.public_view().model_dump(mode="json") for item in items]


@router.get("/organizations/{org_id}/operations/{operation_id}")
async def operation(
    org_id: str,
    operation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
) -> dict[str, Any]:
    _require_ui()
    await _require_owner(org_service, org_id, current_user.user_id)
    item = await asyncio.to_thread(BillingOperationRepository().get_by_id, org_id, operation_id)
    if item is None:
        raise AppException(
            code=ErrorCode.NOT_FOUND,
            status_code=404,
            message="Billing operation not found",
            details={"code": "billing_operation_not_found"},
        )
    return item.public_view().model_dump(mode="json")


def _body(value: Any) -> dict[str, Any]:
    body = value.model_dump(mode="json", exclude_none=True)
    # Product-side saga linkage is never part of PuppyPay's commercial
    # contract. It stays in PuppyOne and is stripped at the BFF boundary.
    body.pop("operation_id", None)
    return body


def _upstream_quote(response: dict[str, Any], *, org_id: str) -> dict[str, Any]:
    raw_quote = response.get("quote", response)
    if not isinstance(raw_quote, dict):
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=502,
            message="Billing provider returned an invalid quote",
            details={"code": "billing_quote_contract_invalid"},
        )
    quote_id = raw_quote.get("quote_id")
    quote_org_id = raw_quote.get("org_id")
    current_plan_id = raw_quote.get("current_plan_id")
    target_plan_id = raw_quote.get("target_plan_id")
    current_seats = raw_quote.get("current_seats")
    target_seats = raw_quote.get("target_seats")
    application_mode = raw_quote.get("application_mode")
    valid = (
        isinstance(quote_id, str)
        and bool(quote_id)
        and len(quote_id) <= 255
        and quote_org_id == org_id
        and isinstance(current_plan_id, str)
        and bool(current_plan_id)
        and isinstance(target_plan_id, str)
        and bool(target_plan_id)
        and isinstance(current_seats, int)
        and not isinstance(current_seats, bool)
        and current_seats >= 0
        and isinstance(target_seats, int)
        and not isinstance(target_seats, bool)
        and target_seats > 0
        and application_mode in {"checkout", "plan_change", "seat_change"}
    )
    if not valid:
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=502,
            message="Billing provider returned inconsistent quote data",
            details={"code": "billing_quote_contract_invalid"},
        )
    return raw_quote


def _source_revision(summary: dict[str, Any]) -> int:
    value = summary.get("source_revision")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=502,
            message="Billing provider returned an invalid source revision",
            details={"code": "billing_summary_contract_invalid"},
        )
    return value


def _validate_prepared_operation(
    operation: BillingOperation,
    *,
    quote_id: str,
    application_mode: Literal["checkout", "plan_change", "seat_change"],
    allowed_kinds: set[str],
    quote_payload: dict[str, Any] | None = None,
) -> BillingOperation:
    if operation.status in {"failed", "canceled"}:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Billing operation is already terminal",
            details={"code": "billing_operation_terminal"},
        )
    if (
        operation.kind not in allowed_kinds
        or operation.quote_id != quote_id
        or operation.request_payload.get("application_mode") != application_mode
        or operation.target_plan_id is None
        or operation.target_seat_quantity is None
        or operation.current_seat_quantity is None
        or operation.baseline_source_revision is None
        or (
            quote_payload is not None
            and (
                operation.target_plan_id != quote_payload["target_plan_id"]
                or operation.target_seat_quantity != quote_payload["target_seats"]
                or operation.current_seat_quantity != quote_payload["current_seats"]
            )
        )
    ):
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Idempotent billing operation does not match this request",
            details={"code": "billing_operation_intent_mismatch"},
        )
    return operation


async def _billing_quote_for_mutation(
    *,
    gateway: PuppyPayGateway,
    org_id: str,
    quote_id: str,
    current_user: CurrentUser,
) -> dict[str, Any]:
    response = await _call(
        gateway,
        "GET",
        (
            f"/api/v1/billing/organizations/{quote(org_id, safe='')}"
            f"/quotes/{quote(quote_id, safe='')}"
        ),
        actor_user_id=current_user.user_id,
        actor_email=current_user.email,
    )
    return _upstream_quote(response, org_id=org_id)


async def _prepare_commercial_operation(
    *,
    repository: BillingOperationRepository,
    gateway: PuppyPayGateway,
    current_user: CurrentUser,
    org_id: str,
    application_mode: Literal["checkout", "plan_change", "seat_change"],
    idempotency_key: str,
    actor_user_id: str,
    quote_id: str,
    requested_plan_id: str | None = None,
    requested_seat_quantity: int | None = None,
) -> BillingOperation:
    allowed_kinds = (
        {"checkout"}
        if application_mode == "checkout"
        else {"plan_change", "seat_increase", "seat_decrease"}
        if application_mode == "plan_change"
        else {"seat_increase", "seat_decrease"}
    )
    existing = await asyncio.to_thread(repository.get_by_key, org_id, idempotency_key)
    if existing is not None:
        return _validate_prepared_operation(
            existing,
            quote_id=quote_id,
            application_mode=application_mode,
            allowed_kinds=allowed_kinds,
        )
    existing_quote_operation = await asyncio.to_thread(
        repository.get_by_quote_id,
        org_id,
        quote_id,
    )
    if existing_quote_operation is not None:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Billing quote is already linked to another durable operation",
            details={
                "code": "billing_quote_already_linked",
                "operation_id": existing_quote_operation.id,
            },
        )

    quote_payload, summary = await asyncio.gather(
        _billing_quote_for_mutation(
            gateway=gateway,
            org_id=org_id,
            quote_id=quote_id,
            current_user=current_user,
        ),
        _billing_summary_for_mutation(
            gateway=gateway,
            org_id=org_id,
            current_user=current_user,
        ),
    )
    if quote_payload["application_mode"] != application_mode:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Billing quote cannot be used for this operation",
            details={"code": "billing_quote_application_mismatch"},
        )
    if application_mode == "checkout" and (
        quote_payload["target_plan_id"] != requested_plan_id
        or quote_payload["target_seats"] != requested_seat_quantity
    ):
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Checkout request does not match its authoritative quote",
            details={"code": "billing_quote_intent_mismatch"},
        )

    current_plan_id = quote_payload["current_plan_id"]
    target_plan_id = quote_payload["target_plan_id"]
    current_seats = quote_payload["current_seats"]
    target_seat_quantity = quote_payload["target_seats"]
    baseline = _source_revision(summary)
    operation_kind: Literal["checkout", "seat_increase", "seat_decrease", "plan_change"]
    if application_mode == "checkout":
        operation_kind = "checkout"
    elif target_plan_id != current_plan_id:
        operation_kind = "plan_change"
    elif target_seat_quantity != current_seats:
        operation_kind = (
            "seat_increase" if target_seat_quantity > current_seats else "seat_decrease"
        )
    else:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Billing quote does not change the subscription",
            details={"code": "billing_quote_noop"},
        )
    if application_mode == "seat_change" and target_plan_id != current_plan_id:
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=502,
            message="Billing provider returned an invalid seat-change quote",
            details={"code": "billing_quote_contract_invalid"},
        )

    operation = await asyncio.to_thread(
        repository.create_commercial_operation,
        org_id=org_id,
        kind=operation_kind,
        idempotency_key=idempotency_key,
        actor_user_id=actor_user_id,
        quote_id=quote_id,
        application_mode=application_mode,
        target_plan_id=target_plan_id,
        current_seat_quantity=current_seats,
        target_seat_quantity=target_seat_quantity,
        baseline_source_revision=baseline,
        response_payload={"schema_version": "1.1", "quote": quote_payload},
    )
    return _validate_prepared_operation(
        operation,
        quote_id=quote_id,
        application_mode=application_mode,
        allowed_kinds=allowed_kinds,
        quote_payload=quote_payload,
    )


async def _prepare_linked_membership_operation(
    *,
    repository: BillingOperationRepository,
    operation: BillingOperation,
    gateway: PuppyPayGateway,
    current_user: CurrentUser,
    org_id: str,
    quote_id: str,
    application_mode: Literal["checkout", "seat_change"],
    requested_plan_id: str | None = None,
    requested_seat_quantity: int | None = None,
) -> BillingOperation:
    if (
        requested_plan_id is not None
        and operation.target_plan_id is not None
        and operation.target_plan_id != requested_plan_id
    ) or (
        requested_seat_quantity is not None
        and operation.target_seat_quantity is not None
        and operation.target_seat_quantity != requested_seat_quantity
    ):
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Checkout request does not match the linked membership operation",
            details={"code": "billing_operation_intent_mismatch"},
        )
    existing_quote_operation = await asyncio.to_thread(
        repository.get_by_quote_id,
        org_id,
        quote_id,
    )
    if existing_quote_operation is not None and existing_quote_operation.id != operation.id:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Billing quote is already linked to another durable operation",
            details={"code": "billing_quote_already_linked"},
        )
    if (
        operation.quote_id == quote_id
        and operation.target_plan_id is not None
        and operation.target_seat_quantity is not None
        and operation.current_seat_quantity is not None
        and operation.baseline_source_revision is not None
        and operation.request_payload.get("application_mode") == application_mode
    ):
        return _validate_prepared_operation(
            operation,
            quote_id=quote_id,
            application_mode=application_mode,
            allowed_kinds={"member_activation", "member_deactivation"},
        )

    quote_payload, summary = await asyncio.gather(
        _billing_quote_for_mutation(
            gateway=gateway,
            org_id=org_id,
            quote_id=quote_id,
            current_user=current_user,
        ),
        _billing_summary_for_mutation(
            gateway=gateway,
            org_id=org_id,
            current_user=current_user,
        ),
    )
    target_plan_id = quote_payload["target_plan_id"]
    target_seat_quantity = quote_payload["target_seats"]
    current_plan_id = quote_payload["current_plan_id"]
    current_seats = quote_payload["current_seats"]
    baseline = _source_revision(summary)
    if (
        quote_payload["application_mode"] != application_mode
        or (requested_plan_id is not None and target_plan_id != requested_plan_id)
        or (requested_seat_quantity is not None and target_seat_quantity != requested_seat_quantity)
        or (application_mode == "seat_change" and target_plan_id != current_plan_id)
        or target_seat_quantity == current_seats
        or (
            operation.target_seat_quantity is not None
            and operation.target_seat_quantity != target_seat_quantity
        )
        or (operation.kind == "member_activation" and target_seat_quantity < current_seats)
        or (operation.kind == "member_deactivation" and target_seat_quantity > current_seats)
    ):
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Seat quote does not match the pending membership operation",
            details={"code": "billing_operation_seat_mismatch"},
        )
    updated = await asyncio.to_thread(
        repository.update_nonterminal,
        operation.id,
        {
            "status": "pending",
            "target_plan_id": target_plan_id,
            "current_seat_quantity": current_seats,
            "target_seat_quantity": target_seat_quantity,
            "quote_id": quote_id,
            "baseline_source_revision": baseline,
            "request_payload": {
                **operation.request_payload,
                "schema_version": "1.1",
                "quote_id": quote_id,
                "application_mode": application_mode,
                "target_plan_id": target_plan_id,
                "target_seat_quantity": target_seat_quantity,
            },
            "last_error": None,
        },
    )
    if updated is None:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Billing operation became terminal before submission",
            details={"code": "billing_operation_terminal"},
        )
    return _validate_prepared_operation(
        updated,
        quote_id=quote_id,
        application_mode=application_mode,
        allowed_kinds={"member_activation", "member_deactivation"},
        quote_payload=quote_payload,
    )


async def _call_commercial_mutation(
    *,
    repository: BillingOperationRepository,
    operation: BillingOperation,
    gateway: PuppyPayGateway,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return await _call(gateway, method, path, **kwargs)
    except AppException as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        if details.get("retryable") is False:
            await asyncio.to_thread(
                repository.update_nonterminal,
                operation.id,
                {
                    "status": "failed",
                    "last_error": str(details.get("code") or "billing_request_failed"),
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )
        raise


async def _submit_commercial_operation(
    *,
    repository: BillingOperationRepository,
    operation: BillingOperation,
    org_id: str,
    quote_payload: dict[str, Any],
    response_payload: dict[str, Any],
) -> BillingOperation:
    if (
        operation.quote_id != quote_payload["quote_id"]
        or operation.target_plan_id != quote_payload["target_plan_id"]
        or operation.target_seat_quantity != quote_payload["target_seats"]
        or operation.current_seat_quantity != quote_payload["current_seats"]
    ):
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=502,
            message="Billing provider response does not match the durable operation intent",
            details={"code": "billing_operation_provider_mismatch"},
        )
    if operation.status not in {"confirmed", "failed", "canceled"}:
        updated = await asyncio.to_thread(
            repository.update_nonterminal,
            operation.id,
            {
                "status": "submitted",
                "response_payload": response_payload,
                "last_error": None,
            },
        )
        if updated is not None:
            operation = updated
    return await asyncio.to_thread(
        repository.reconcile_from_entitlement,
        org_id=org_id,
        operation_id=operation.id,
    )


async def _billing_summary_for_mutation(
    *,
    gateway: PuppyPayGateway,
    org_id: str,
    current_user: CurrentUser,
) -> dict[str, Any]:
    return await _call(
        gateway,
        "GET",
        f"/api/v1/billing/organizations/{quote(org_id, safe='')}/summary",
        actor_user_id=current_user.user_id,
        actor_email=current_user.email,
    )


def _seat_operation(
    *,
    repository: BillingOperationRepository,
    org_id: str,
    operation_id: str,
    target_seat_quantity: int | None = None,
    quote_id: str | None = None,
):
    operation = repository.get_by_id(org_id, operation_id)
    if operation is None:
        raise AppException(
            code=ErrorCode.NOT_FOUND,
            status_code=404,
            message="Billing operation not found",
            details={"code": "billing_operation_not_found"},
        )
    if operation.kind not in {"member_activation", "member_deactivation"}:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Billing operation is not a seat operation",
            details={"code": "billing_operation_kind_mismatch"},
        )
    if operation.status in {"confirmed", "failed", "canceled"}:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Billing operation is already terminal",
            details={"code": "billing_operation_terminal"},
        )
    if target_seat_quantity is not None and operation.target_seat_quantity != target_seat_quantity:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Seat quote does not match the pending operation",
            details={"code": "billing_operation_seat_mismatch"},
        )
    if quote_id is not None and operation.quote_id not in {None, quote_id}:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Quote does not match the pending operation",
            details={"code": "billing_operation_quote_mismatch"},
        )
    return operation


@router.post("/organizations/{org_id}/plan/quote")
async def plan_quote(
    org_id: str,
    payload: PlanQuoteRequest,
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    return await _authorized_org_call(
        gateway=gateway,
        org_service=org_service,
        current_user=current_user,
        authorization=authorization,
        org_id=org_id,
        method="POST",
        suffix="plan/quote",
        body=_body(payload),
        idempotency_key=_idempotency_key(idempotency_key),
        write=True,
    )


@router.post("/organizations/{org_id}/seats/quote")
async def seat_quote(
    org_id: str,
    payload: SeatQuoteRequest,
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    _require_writes()
    await _require_owner(org_service, org_id, current_user.user_id)
    del authorization
    repository: BillingOperationRepository | None = None
    operation = None
    if payload.operation_id:
        repository = BillingOperationRepository()
        operation = await asyncio.to_thread(
            _seat_operation,
            repository=repository,
            org_id=org_id,
            operation_id=payload.operation_id,
            target_seat_quantity=payload.seat_quantity,
        )
    response = await _call(
        gateway,
        "POST",
        f"/api/v1/billing/organizations/{quote(org_id, safe='')}/seats/quote",
        actor_user_id=current_user.user_id,
        actor_email=current_user.email,
        idempotency_key=_idempotency_key(idempotency_key),
        body={"seat_quantity": payload.seat_quantity},
    )
    if repository is not None and operation is not None:
        quote_payload = _upstream_quote(response, org_id=org_id)
        if quote_payload["target_seats"] != payload.seat_quantity:
            raise AppException(
                code=ErrorCode.INTERNAL_SERVER_ERROR,
                status_code=502,
                message="Billing provider returned an inconsistent seat quote",
                details={"code": "billing_quote_contract_invalid"},
            )
        updated = await asyncio.to_thread(
            repository.update_nonterminal,
            operation.id,
            {
                "status": "quoted",
                "quote_id": quote_payload["quote_id"],
                "target_plan_id": quote_payload["target_plan_id"],
                "current_seat_quantity": quote_payload["current_seats"],
                "target_seat_quantity": quote_payload["target_seats"],
                "request_payload": {
                    **operation.request_payload,
                    "schema_version": "1.1",
                    "quote_id": quote_payload["quote_id"],
                    "application_mode": quote_payload["application_mode"],
                    "target_plan_id": quote_payload["target_plan_id"],
                    "target_seat_quantity": quote_payload["target_seats"],
                },
                "response_payload": {**operation.response_payload, "quote": response},
                "last_error": None,
            },
        )
        if updated is None:
            raise AppException(
                code=ErrorCode.BAD_REQUEST,
                status_code=409,
                message="Billing operation became terminal while creating the quote",
                details={"code": "billing_operation_terminal"},
            )
    return response


async def _idempotent_mutation(
    *,
    org_id: str,
    suffix: str,
    payload: Any,
    authorization: str,
    idempotency_key: str | None,
    current_user: CurrentUser,
    org_service: OrganizationService,
    gateway: PuppyPayGateway,
    method: str = "POST",
) -> dict[str, Any]:
    return await _authorized_org_call(
        gateway=gateway,
        org_service=org_service,
        current_user=current_user,
        authorization=authorization,
        org_id=org_id,
        method=method,
        suffix=suffix,
        body=_body(payload) if payload is not None else None,
        idempotency_key=_idempotency_key(idempotency_key),
        write=True,
    )


@router.post("/organizations/{org_id}/checkout")
async def checkout(
    org_id: str,
    payload: CheckoutRequest,
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    _require_writes()
    await _require_owner(org_service, org_id, current_user.user_id)
    del authorization
    key = _idempotency_key(idempotency_key)
    if payload.quote_id is None:
        raise AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="Desktop checkout requires a prior authoritative quote",
            details={"code": "billing_quote_required"},
        )
    repository = BillingOperationRepository()
    if payload.operation_id:
        item = await asyncio.to_thread(
            _seat_operation,
            repository=repository,
            org_id=org_id,
            operation_id=payload.operation_id,
            quote_id=payload.quote_id,
        )
        item = await _prepare_linked_membership_operation(
            repository=repository,
            operation=item,
            gateway=gateway,
            current_user=current_user,
            org_id=org_id,
            quote_id=payload.quote_id,
            application_mode="checkout",
            requested_plan_id=payload.plan_id,
            requested_seat_quantity=payload.seat_quantity,
        )
    else:
        item = await _prepare_commercial_operation(
            repository=repository,
            gateway=gateway,
            current_user=current_user,
            org_id=org_id,
            application_mode="checkout",
            idempotency_key=key,
            actor_user_id=current_user.user_id,
            quote_id=payload.quote_id,
            requested_plan_id=payload.plan_id,
            requested_seat_quantity=payload.seat_quantity,
        )
    response = await _call_commercial_mutation(
        repository=repository,
        operation=item,
        gateway=gateway,
        method="POST",
        path=f"/api/v1/billing/organizations/{quote(org_id, safe='')}/checkout",
        actor_user_id=current_user.user_id,
        actor_email=current_user.email,
        # The durable operation, rather than a later client retry, owns the
        # cross-service idempotency identity. This is especially important for
        # membership-linked checkout/seat flows whose UI request key is not
        # itself persisted on the saga row.
        idempotency_key=item.idempotency_key,
        body=_body(payload),
    )
    quote_payload = _upstream_quote(response, org_id=org_id)
    if quote_payload["application_mode"] != "checkout":
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=502,
            message="Billing provider returned an invalid checkout quote",
            details={"code": "billing_checkout_contract_invalid"},
        )
    checkout_id = response.get("checkout_id")
    if not isinstance(checkout_id, str) or not checkout_id:
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=502,
            message="Billing provider returned an invalid checkout",
            details={"code": "billing_checkout_contract_invalid"},
        )
    item = await _submit_commercial_operation(
        repository=repository,
        operation=item,
        org_id=org_id,
        quote_payload=quote_payload,
        response_payload={"schema_version": "1.1", "checkout_id": checkout_id},
    )
    return {**response, "operation": item.public_view().model_dump(mode="json")}


@router.post("/organizations/{org_id}/plan/change")
async def plan_change(
    org_id: str,
    payload: QuoteApplyRequest,
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    _require_writes()
    await _require_owner(org_service, org_id, current_user.user_id)
    del authorization
    key = _idempotency_key(idempotency_key)
    repository = BillingOperationRepository()
    item = await _prepare_commercial_operation(
        repository=repository,
        gateway=gateway,
        current_user=current_user,
        org_id=org_id,
        application_mode="plan_change",
        idempotency_key=key,
        actor_user_id=current_user.user_id,
        quote_id=payload.quote_id,
    )
    response = await _call_commercial_mutation(
        repository=repository,
        operation=item,
        gateway=gateway,
        method="POST",
        path=f"/api/v1/billing/organizations/{quote(org_id, safe='')}/plan/change",
        actor_user_id=current_user.user_id,
        actor_email=current_user.email,
        idempotency_key=item.idempotency_key,
        body={"quote_id": payload.quote_id},
    )
    quote_payload = _upstream_quote(response, org_id=org_id)
    if quote_payload["application_mode"] != "plan_change":
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=502,
            message="Billing provider returned an invalid plan-change quote",
            details={"code": "billing_quote_contract_invalid"},
        )
    item = await _submit_commercial_operation(
        repository=repository,
        operation=item,
        org_id=org_id,
        quote_payload=quote_payload,
        response_payload={"schema_version": "1.1", "application": response},
    )
    return {**response, "operation": item.public_view().model_dump(mode="json")}


@router.post("/organizations/{org_id}/seats/change")
async def seats_change(
    org_id: str,
    payload: QuoteApplyRequest,
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    _require_writes()
    await _require_owner(org_service, org_id, current_user.user_id)
    del authorization
    key = _idempotency_key(idempotency_key)
    repository = BillingOperationRepository()
    operation = None
    if payload.operation_id:
        operation = await asyncio.to_thread(
            _seat_operation,
            repository=repository,
            org_id=org_id,
            operation_id=payload.operation_id,
            quote_id=payload.quote_id,
        )
        operation = await _prepare_linked_membership_operation(
            repository=repository,
            operation=operation,
            gateway=gateway,
            current_user=current_user,
            org_id=org_id,
            quote_id=payload.quote_id,
            application_mode="seat_change",
        )
    else:
        operation = await _prepare_commercial_operation(
            repository=repository,
            gateway=gateway,
            current_user=current_user,
            org_id=org_id,
            application_mode="seat_change",
            idempotency_key=key,
            actor_user_id=current_user.user_id,
            quote_id=payload.quote_id,
        )
    response = await _call_commercial_mutation(
        repository=repository,
        operation=operation,
        gateway=gateway,
        method="POST",
        path=f"/api/v1/billing/organizations/{quote(org_id, safe='')}/seats/change",
        actor_user_id=current_user.user_id,
        actor_email=current_user.email,
        idempotency_key=operation.idempotency_key,
        body={"quote_id": payload.quote_id},
    )
    quote_payload = _upstream_quote(response, org_id=org_id)
    if quote_payload["application_mode"] != "seat_change":
        raise AppException(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=502,
            message="Billing provider returned an invalid seat-change quote",
            details={"code": "billing_quote_contract_invalid"},
        )
    item = await _submit_commercial_operation(
        repository=repository,
        operation=operation,
        org_id=org_id,
        quote_payload=quote_payload,
        response_payload={
            "schema_version": "1.1",
            **(operation.response_payload if operation is not None else {}),
            "application": response,
        },
    )
    return {**response, "operation": item.public_view().model_dump(mode="json")}


@router.post("/organizations/{org_id}/subscription/cancel")
async def cancel(
    org_id: str,
    payload: CancellationRequest,
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    return await _idempotent_mutation(
        org_id=org_id,
        suffix="subscription/cancel",
        payload=payload,
        authorization=authorization,
        idempotency_key=idempotency_key,
        current_user=current_user,
        org_service=org_service,
        gateway=gateway,
    )


@router.post("/organizations/{org_id}/portal")
async def portal(
    org_id: str,
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    return await _idempotent_mutation(
        org_id=org_id,
        suffix="portal",
        payload=None,
        authorization=authorization,
        idempotency_key=idempotency_key,
        current_user=current_user,
        org_service=org_service,
        gateway=gateway,
    )


@router.post("/organizations/{org_id}/runtime/top-up")
async def runtime_top_up(
    org_id: str,
    payload: TopUpRequest,
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    return await _idempotent_mutation(
        org_id=org_id,
        suffix="runtime/top-up",
        payload=payload,
        authorization=authorization,
        idempotency_key=idempotency_key,
        current_user=current_user,
        org_service=org_service,
        gateway=gateway,
    )


@router.put("/organizations/{org_id}/runtime/overage")
async def runtime_overage(
    org_id: str,
    payload: RuntimeOverageRequest,
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    current_user: CurrentUser = Depends(get_current_user),
    org_service: OrganizationService = Depends(get_org_service),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
) -> dict[str, Any]:
    return await _idempotent_mutation(
        org_id=org_id,
        suffix="runtime/overage",
        payload=payload,
        authorization=authorization,
        idempotency_key=idempotency_key,
        current_user=current_user,
        org_service=org_service,
        gateway=gateway,
        method="PUT",
    )
