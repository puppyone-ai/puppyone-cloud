from __future__ import annotations

import json
from typing import Any

import pytest

from src.config import settings
from src.platform.billing.gateway import BillingGatewayError
from src.platform.billing.operations import BillingOperation
from src.platform.billing.provisioning import EntitlementProvisioningService
from src.platform.entitlements.models import (
    EntitlementPublicationAck,
    EntitlementSnapshot,
    EntitlementUpsert,
)
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


class _Published(_Entitlements):
    """The table already holds ``stored``; publishing an older or conflicting revision fails."""

    def __init__(self, stored: dict[str, Any] | None) -> None:
        super().__init__()
        self.stored = stored

    def publish(self, snapshot: EntitlementUpsert) -> EntitlementPublicationAck:
        self.received = snapshot
        raise RuntimeError("stale entitlement revision")

    def published_snapshot(self, org_id: str):
        return None if self.stored is None else EntitlementSnapshot(**self.stored)


def _provisioning(
    entitlements: _Entitlements,
) -> tuple[EntitlementProvisioningService, _Operations]:
    operations = _Operations()
    service = EntitlementProvisioningService(
        gateway=_Gateway(), operation_repository=operations, entitlement_service=entitlements
    )
    return service, operations


@pytest.mark.asyncio
async def test_provisioning_superseded_by_a_purchase_is_complete(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    paid = {**signed_free_snapshot(), "plan_id": "team_v2", "source_revision": 2}
    service, operations = _provisioning(_Published(paid))

    assert await service.ensure(org_id="org-1", actor_user_id="user-1") is True

    confirmed = operations.updates[-1]
    assert (confirmed["status"], confirmed["confirmed_revision"]) == ("confirmed", 2)
    assert confirmed["response_payload"]["payload_hash"] == paid["payload_hash"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_revision", [None, 1])
async def test_provisioning_that_could_not_publish_is_retried(monkeypatch, stored_revision) -> None:
    # Nothing published yet, or the same revision with other content: both
    # are real failures, not a newer plan.
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    stored = None
    if stored_revision is not None:
        stored = {**signed_free_snapshot(), "source_revision": stored_revision}
    service, operations = _provisioning(_Published(stored))

    assert await service.ensure(org_id="org-1", actor_user_id="user-1") is False
    assert operations.updates[-1]["status"] == "failed"
    assert operations.updates[-1]["last_error"] == "RuntimeError"
