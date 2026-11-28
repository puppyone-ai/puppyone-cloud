"""Fakes shared by the billing router tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from src.platform.billing.operations import BillingOperation
from src.platform.entitlements.models import EntitlementUpsert


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


def signed_free_snapshot(org_id: str = "org-1") -> dict[str, Any]:
    unsigned = {
        "org_id": org_id,
        "schema_version": "1.0",
        "plan_id": "free",
        "status": "free",
        "source": "puppypay",
        "entitlements": {
            "features": {
                "access_surface.agent": False,
                "access_surface.mcp": False,
                "access_surface.sandbox": False,
                "automation.hosted": False,
                "remote_workspace.create": False,
                "scope_sandbox.connect": False,
            },
            "limits": {
                "projects.max": 1,
                "repo_scopes.max_per_project": 2,
                "storage.max_bytes": 500 * 1024**2,
                "upload.max_single_file_bytes": 50 * 1024**2,
                "seats.purchased": 1,
                "runtime.included_units": 0,
            },
            "allow": {"access_surface_kinds": ["git_remote", "cli", "direct"]},
        },
        "seat_quantity": 1,
        "catalog_version": "2026-07-14.1",
        "source_revision": 1,
        "effective_at": datetime(2026, 7, 14, tzinfo=UTC),
        "current_period_end": None,
        "effective_until": None,
        "source_event_id": None,
        "event_type": "organization.provisioned",
    }
    canonical = json.dumps(
        EntitlementUpsert.model_construct(**unsigned, payload_hash="0" * 64).model_dump(
            mode="json",
            # Schema 1.0 predates quote correlation, so it is not signed.
            exclude={"payload_hash", "source_quote_id"},
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return {**unsigned, "payload_hash": hashlib.sha256(canonical.encode()).hexdigest()}
