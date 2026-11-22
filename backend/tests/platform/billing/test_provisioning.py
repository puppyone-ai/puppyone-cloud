from __future__ import annotations

import json
from typing import Any

import pytest

from src.config import settings
from src.platform.billing.gateway import BillingGatewayError
from src.platform.billing.operations import BillingOperation
from src.platform.billing.provisioning import EntitlementProvisioningService
from src.platform.entitlements.models import EntitlementPublicationAck, EntitlementUpsert
from tests.platform.billing.support import signed_free_snapshot


class _Gateway:
    def __init__(self, *, failure: bool = False) -> None:
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"method": method, "path": path, **kwargs})
        if self.failure:
            raise BillingGatewayError(503, {"error": {"code": "unavailable"}})
        return signed_free_snapshot(str(kwargs["body"]["org_id"]))


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
