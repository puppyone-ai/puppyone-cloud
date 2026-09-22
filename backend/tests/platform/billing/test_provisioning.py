from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from src.config import settings
from src.platform.billing.gateway import BillingGatewayError
from src.platform.billing.operations import BillingOperation
from src.platform.billing.provisioning import EntitlementProvisioningService
from src.platform.entitlements.models import EntitlementPublicationAck, EntitlementUpsert


def _snapshot(org_id: str = "org-1") -> dict[str, Any]:
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
            exclude={"payload_hash"},
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return {**unsigned, "payload_hash": hashlib.sha256(canonical.encode()).hexdigest()}


class _Gateway:
    def __init__(self, *, failure: bool = False) -> None:
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"method": method, "path": path, **kwargs})
        if self.failure:
            raise BillingGatewayError(503, {"error": {"code": "unavailable"}})
        return _snapshot(str(kwargs["body"]["org_id"]))


class _Operations:
    def __init__(self) -> None:
        self.operation = BillingOperation(
            id="operation-1",
            org_id="org-1",
            kind="entitlement_provision",
            status="pending",
            idempotency_key="entitlement-provision:v1",
            actor_user_id="user-1",
            attempts=0,
        )
        self.updates: list[dict[str, Any]] = []

    def enqueue_entitlement_provisioning(self, **kwargs: Any) -> BillingOperation:
        assert kwargs == {"org_id": "org-1", "actor_user_id": "user-1"}
        return self.operation

    def update(self, operation_id: str, values: dict[str, Any]) -> BillingOperation:
        assert operation_id == self.operation.id
        self.updates.append(values)
        return self.operation.model_copy(update=values)


class _Entitlements:
    def __init__(self) -> None:
        self.received: EntitlementUpsert | None = None

    def publish(self, snapshot: EntitlementUpsert) -> EntitlementPublicationAck:
        self.received = snapshot
        return EntitlementPublicationAck(
            outcome="inserted",
            source_revision=snapshot.source_revision,
            payload_hash=snapshot.payload_hash,
            snapshot=snapshot.model_dump(mode="json"),
        )


@pytest.mark.asyncio
async def test_provisioning_projects_authoritative_snapshot_and_confirms_operation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    gateway = _Gateway()
    operations = _Operations()
    entitlements = _Entitlements()
    service = EntitlementProvisioningService(
        gateway=gateway,
        operation_repository=operations,
        entitlement_service=entitlements,
    )

    assert await service.ensure(org_id="org-1", actor_user_id="user-1") is True

    assert gateway.calls[0]["path"] == "/internal/v1/billing/organizations/provision"
    assert gateway.calls[0]["idempotency_key"] == "entitlement-provision:v1"
    assert entitlements.received is not None
    assert entitlements.received.plan_id == "free"
    assert operations.updates[-1]["status"] == "confirmed"
    assert operations.updates[-1]["confirmed_revision"] == 1


@pytest.mark.asyncio
async def test_provisioning_failure_is_redacted_and_left_for_retry(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    operations = _Operations()
    service = EntitlementProvisioningService(
        gateway=_Gateway(failure=True),
        operation_repository=operations,
        entitlement_service=_Entitlements(),
    )

    assert await service.ensure(org_id="org-1", actor_user_id="user-1") is False

    failed = operations.updates[-1]
    assert failed["status"] == "failed"
    assert failed["last_error"] == "BillingGatewayError"
    assert "unavailable" not in json.dumps(failed)
    assert failed["next_attempt_at"]
