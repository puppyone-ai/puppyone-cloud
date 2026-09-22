from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.infra.supabase.client import SupabaseClient

BillingOperationKind = Literal[
    "checkout",
    "seat_increase",
    "seat_decrease",
    "plan_change",
    "member_activation",
    "member_deactivation",
    "entitlement_provision",
]
BillingOperationStorageStatus = Literal[
    "pending",
    "quoted",
    "awaiting_confirmation",
    "submitted",
    "confirmed",
    "failed",
    "canceled",
]
BillingOperationState = Literal[
    "pending",
    "requires_action",
    "processing",
    "retryable_failed",
    "succeeded",
    "canceled",
    "failed",
]


class PublicBillingOperation(BaseModel):
    """Stable Desktop contract; storage/worker statuses never define UI semantics."""

    model_config = ConfigDict(extra="forbid")

    id: str
    org_id: str
    kind: BillingOperationKind
    state: BillingOperationState
    terminal: bool
    retryable: bool
    action_required: bool
    target_plan_id: str | None = None
    current_seat_quantity: int | None = None
    target_seat_quantity: int | None = None
    quote_id: str | None = None
    confirmed_revision: int | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class BillingOperation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    org_id: str
    kind: BillingOperationKind
    status: BillingOperationStorageStatus
    idempotency_key: str
    actor_user_id: str | None = None
    subject_user_id: str | None = None
    invitation_id: str | None = None
    target_plan_id: str | None = None
    current_seat_quantity: int | None = None
    target_seat_quantity: int | None = None
    quote_id: str | None = None
    baseline_source_revision: int | None = None
    confirmed_revision: int | None = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response_payload: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    def public_view(self) -> PublicBillingOperation:
        state, terminal, retryable, action_required = _public_lifecycle(self)
        return PublicBillingOperation(
            id=self.id,
            org_id=self.org_id,
            kind=self.kind,
            state=state,
            terminal=terminal,
            retryable=retryable,
            action_required=action_required,
            target_plan_id=self.target_plan_id,
            current_seat_quantity=self.current_seat_quantity,
            target_seat_quantity=self.target_seat_quantity,
            quote_id=self.quote_id,
            confirmed_revision=self.confirmed_revision,
            error_code=self.last_error,
            created_at=self.created_at,
            updated_at=self.updated_at,
            completed_at=self.completed_at,
        )


def _public_lifecycle(
    operation: BillingOperation,
) -> tuple[BillingOperationState, bool, bool, bool]:
    if operation.status == "confirmed" and operation.completed_at is not None:
        return "succeeded", True, False, False
    if operation.status == "confirmed":
        # Seat admission reserves capacity before the capability mutation and
        # completes the same row immediately afterwards. A process crash can
        # leave that reservation as a short lease, so it is not yet a public
        # terminal success merely because its storage status is confirmed.
        return "processing", False, True, False
    if operation.status == "canceled":
        return "canceled", True, False, False
    if operation.status == "failed":
        if operation.kind == "entitlement_provision":
            return "retryable_failed", False, True, False
        return "failed", True, False, False
    if operation.status in {"quoted", "awaiting_confirmation"}:
        return "requires_action", False, False, True
    if operation.status == "submitted":
        return "processing", False, True, False
    return "pending", False, True, False


class BillingOperationRepository:
    TABLE = "organization_billing_operations"

    def __init__(self, supabase_client: SupabaseClient | None = None):
        self._client = (supabase_client or SupabaseClient()).get_client()

    def create_or_get(self, values: dict[str, Any]) -> BillingOperation:
        org_id = str(values["org_id"])
        key = str(values["idempotency_key"])
        existing = self.get_by_key(org_id, key)
        if existing is not None:
            return existing
        try:
            response = self._client.table(self.TABLE).insert(values).execute()
        except Exception:
            # A concurrent retry may have won the unique (org_id, key) race.
            existing = self.get_by_key(org_id, key)
            if existing is None:
                raise
            return existing
        return BillingOperation.model_validate(response.data[0])

    def enqueue_entitlement_provisioning(
        self,
        *,
        org_id: str,
        actor_user_id: str | None = None,
    ) -> BillingOperation:
        return self.create_or_get(
            {
                "org_id": org_id,
                "kind": "entitlement_provision",
                "status": "pending",
                "idempotency_key": "entitlement-provision:v1",
                "actor_user_id": actor_user_id,
                "request_payload": {"schema_version": "1.0"},
            }
        )

    def enqueue_missing_entitlement_provisioning(self, *, limit: int = 100) -> int:
        response = self._client.rpc(
            "enqueue_missing_entitlement_provisioning",
            {"p_limit": max(1, min(limit, 1000))},
        ).execute()
        data = response.data
        if isinstance(data, list):
            data = data[0] if data else 0
        return int(data or 0)

    def claim_entitlement_provisioning(
        self,
        *,
        limit: int = 25,
        lease_seconds: int = 60,
    ) -> list[BillingOperation]:
        response = self._client.rpc(
            "claim_entitlement_provisioning_batch",
            {
                "p_limit": max(1, min(limit, 100)),
                "p_lease_seconds": max(10, min(lease_seconds, 3600)),
            },
        ).execute()
        return [BillingOperation.model_validate(row) for row in response.data or []]

    def claim_seat_proposals(
        self,
        *,
        limit: int = 25,
        lease_seconds: int = 60,
    ) -> list[BillingOperation]:
        response = self._client.rpc(
            "claim_seat_proposal_batch",
            {
                "p_limit": max(1, min(limit, 100)),
                "p_lease_seconds": max(10, min(lease_seconds, 3600)),
            },
        ).execute()
        return [BillingOperation.model_validate(row) for row in response.data or []]

    def get_by_key(self, org_id: str, key: str) -> BillingOperation | None:
        response = (
            self._client.table(self.TABLE)
            .select("*")
            .eq("org_id", org_id)
            .eq("idempotency_key", key)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return BillingOperation.model_validate(rows[0]) if rows else None

    def get_by_id(self, org_id: str, operation_id: str) -> BillingOperation | None:
        response = (
            self._client.table(self.TABLE)
            .select("*")
            .eq("org_id", org_id)
            .eq("id", operation_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return BillingOperation.model_validate(rows[0]) if rows else None

    def get_by_quote_id(self, org_id: str, quote_id: str) -> BillingOperation | None:
        response = (
            self._client.table(self.TABLE)
            .select("*")
            .eq("org_id", org_id)
            .eq("quote_id", quote_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return BillingOperation.model_validate(rows[0]) if rows else None

    def update(self, operation_id: str, values: dict[str, Any]) -> BillingOperation:
        response = self._client.table(self.TABLE).update(values).eq("id", operation_id).execute()
        return BillingOperation.model_validate(response.data[0])

    def update_nonterminal(
        self,
        operation_id: str,
        values: dict[str, Any],
    ) -> BillingOperation | None:
        """Advance an operation without ever overwriting a terminal decision."""

        response = (
            self._client.table(self.TABLE)
            .update(values)
            .eq("id", operation_id)
            .in_("status", ["pending", "quoted", "awaiting_confirmation", "submitted"])
            .execute()
        )
        rows = response.data or []
        return BillingOperation.model_validate(rows[0]) if rows else None

    def update_claimed_seat_proposal(
        self,
        operation: BillingOperation,
        values: dict[str, Any],
    ) -> BillingOperation | None:
        """Finish a proposal only if no owner/manual flow changed the row.

        `updated_at` is the lease fencing token advanced by the claim RPC. A
        concurrent Desktop quote therefore wins without a background worker
        regressing its newer quote or application state.
        """

        query = (
            self._client.table(self.TABLE)
            .update(values)
            .eq("id", operation.id)
            .in_("status", ["pending", "awaiting_confirmation"])
        )
        if operation.updated_at is not None:
            query = query.eq("updated_at", operation.updated_at.isoformat())
        response = query.execute()
        rows = response.data or []
        return BillingOperation.model_validate(rows[0]) if rows else None

    def list_for_org(self, org_id: str, *, limit: int = 100) -> list[BillingOperation]:
        response = (
            self._client.table(self.TABLE)
            .select("*")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(max(1, min(limit, 200)))
            .execute()
        )
        return [BillingOperation.model_validate(row) for row in response.data or []]

    def reconcile_from_entitlement(
        self,
        *,
        org_id: str,
        operation_id: str,
    ) -> BillingOperation:
        response = self._client.rpc(
            "reconcile_billing_operation_from_entitlement",
            {"p_org_id": org_id, "p_operation_id": operation_id},
        ).execute()
        rows = response.data or []
        if isinstance(rows, dict):
            rows = [rows]
        if rows:
            return BillingOperation.model_validate(rows[0])
        operation = self.get_by_id(org_id, operation_id)
        if operation is None:
            raise RuntimeError("billing operation disappeared during reconciliation")
        return operation

    def create_commercial_operation(
        self,
        *,
        org_id: str,
        kind: Literal["checkout", "seat_increase", "seat_decrease", "plan_change"],
        idempotency_key: str,
        actor_user_id: str,
        quote_id: str,
        application_mode: Literal["checkout", "plan_change", "seat_change"],
        target_plan_id: str,
        current_seat_quantity: int,
        target_seat_quantity: int,
        baseline_source_revision: int,
        response_payload: dict[str, Any],
    ) -> BillingOperation:
        operation = self.create_or_get(
            {
                "org_id": org_id,
                "kind": kind,
                # The durable intent is committed before the provider call.
                # A correlated webhook may therefore safely win the race with
                # the HTTP response without leaving an untracked side effect.
                "status": "pending",
                "idempotency_key": idempotency_key,
                "actor_user_id": actor_user_id,
                "target_plan_id": target_plan_id,
                "current_seat_quantity": current_seat_quantity,
                "target_seat_quantity": target_seat_quantity,
                "quote_id": quote_id,
                "baseline_source_revision": baseline_source_revision,
                "request_payload": {
                    "schema_version": "1.1",
                    "quote_id": quote_id,
                    "application_mode": application_mode,
                    "target_plan_id": target_plan_id,
                    "target_seat_quantity": target_seat_quantity,
                },
                "response_payload": response_payload,
            }
        )
        expected = (kind, quote_id, application_mode, target_plan_id, target_seat_quantity)
        actual = (
            operation.kind,
            operation.quote_id,
            operation.request_payload.get("application_mode"),
            operation.target_plan_id,
            operation.target_seat_quantity,
        )
        if actual != expected:
            raise ValueError("billing operation idempotency payload mismatch")
        return operation

    def count_pending(self, org_id: str) -> int:
        response = (
            self._client.table(self.TABLE)
            .select("id", count="exact")
            .eq("org_id", org_id)
            .in_("status", ["pending", "awaiting_confirmation", "quoted", "submitted"])
            .execute()
        )
        return int(response.count or 0)

    def reserve_member_activation(
        self,
        *,
        org_id: str,
        subject_user_id: str,
        actor_user_id: str,
        invitation_id: str | None,
        role: str,
        idempotency_key: str,
    ) -> BillingOperation:
        response = self._client.rpc(
            "reserve_billable_member_activation",
            {
                "p_org_id": org_id,
                "p_subject_user_id": subject_user_id,
                "p_actor_user_id": actor_user_id,
                "p_invitation_id": invitation_id,
                "p_role": role,
                "p_idempotency_key": idempotency_key,
            },
        ).execute()
        data = response.data
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            raise RuntimeError("seat admission reservation returned an invalid response")
        return BillingOperation.model_validate(data)
