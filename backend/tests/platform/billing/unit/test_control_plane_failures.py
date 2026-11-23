from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.config import settings
from src.platform.billing.gateway import BillingGatewayError, PuppyPayGateway
from src.platform.billing.operations import BillingOperation
from src.platform.billing.provisioning import EntitlementProvisioningService
from src.platform.billing.seat_proposals import SeatProposalService

SUMMARY = "/api/v1/billing/organizations/org-1/summary"


def gateway(handler) -> PuppyPayGateway:
    return PuppyPayGateway(
        base_url="https://pay.example.test",
        internal_secret="s" * 32,
        transport=httpx.MockTransport(handler),
    )


async def refused(client: PuppyPayGateway, method: str = "GET", **kwargs: Any):
    with pytest.raises(BillingGatewayError) as caught:
        await client.request(method, SUMMARY, **kwargs)
    return caught.value


@pytest.mark.asyncio
async def test_a_lost_connection_is_retried_once_for_a_safe_request() -> None:
    attempts: list[int] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, json={"plan_id": "team"})

    assert (await gateway(flaky).request("GET", SUMMARY))["plan_id"] == "team"
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_puppypay_that_never_answers_is_unavailable() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    error = await refused(gateway(down))
    assert error.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"x" * (1024 * 1024 + 1)),
        httpx.Response(200, content=b"<html>proxy error</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_an_answer_puppypay_would_never_give_is_refused(response) -> None:
    error = await refused(gateway(lambda _: response))
    assert error.status_code == 502


@pytest.mark.asyncio
async def test_billing_without_a_puppypay_address_is_unconfigured() -> None:
    client = PuppyPayGateway(base_url="", transport=httpx.MockTransport(lambda _: None))
    error = await refused(client)
    assert error.status_code == 503
    assert error.payload["error"]["code"] == "billing_unconfigured"


@pytest.mark.asyncio
async def test_a_path_outside_the_billing_api_is_never_called() -> None:
    calls: list[str] = []
    client = gateway(lambda request: calls.append(str(request.url)) or httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="allow-list"):
        await client.request("GET", "/admin/users")
    assert calls == []


class _ProvisionOperations:
    def __init__(self, claimed: list[BillingOperation]) -> None:
        self.claimed = claimed
        self.updates: list[tuple[str, dict]] = []
        self.enqueued: list[int] = []

    def enqueue_missing_entitlement_provisioning(self, *, limit: int) -> int:
        self.enqueued.append(limit)
        return len(self.claimed)

    def claim_entitlement_provisioning(self, *, limit: int, lease_seconds: int):
        return self.claimed

    def update(self, operation_id: str, values: dict) -> None:
        self.updates.append((operation_id, values))


class _Provisioning:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        answer = self.answers[kwargs["body"]["org_id"]]
        if isinstance(answer, Exception):
            raise answer
        return answer


def provisioning(org_id: str) -> BillingOperation:
    return BillingOperation(
        id=f"op-{org_id}",
        org_id=org_id,
        kind="entitlement_provision",
        status="pending",
        idempotency_key=f"entitlement-provision:{org_id}",
        actor_user_id="user-1",
        attempts=2,
    )


@pytest.mark.asyncio
async def test_recovery_provisions_every_space_it_claims_and_retries_the_rest(monkeypatch) -> None:
    from tests.platform.billing.support import signed_free_snapshot

    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    operations = _ProvisionOperations([provisioning("org-a"), provisioning("org-b")])

    class _Published:
        def publish(self, snapshot):
            from src.platform.entitlements.models import EntitlementPublicationAck

            return EntitlementPublicationAck(
                outcome="inserted",
                source_revision=snapshot.source_revision,
                payload_hash=snapshot.payload_hash,
                snapshot=snapshot.model_dump(mode="json"),
            )

    service = EntitlementProvisioningService(
        gateway=_Provisioning(
            {
                "org-a": signed_free_snapshot("org-a"),
                "org-b": BillingGatewayError(503, {"error": {"code": "unavailable"}}),
            }
        ),
        operation_repository=operations,
        entitlement_service=_Published(),
    )

    result = await service.recover_once(limit=10)

    assert result == {"enqueued": 2, "claimed": 2, "succeeded": 1, "failed": 1}
    statuses = {operation_id: values["status"] for operation_id, values in operations.updates}
    assert statuses == {"op-org-a": "confirmed", "op-org-b": "failed"}
    failed = dict(operations.updates)["op-org-b"]
    assert failed["last_error"] == "BillingGatewayError"


@pytest.mark.asyncio
async def test_a_snapshot_for_another_space_is_never_published(monkeypatch) -> None:
    from tests.platform.billing.support import signed_free_snapshot

    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db", raising=False)
    operations = _ProvisionOperations([provisioning("org-a")])

    class _Never:
        def publish(self, snapshot):
            raise AssertionError("published a foreign snapshot")

    service = EntitlementProvisioningService(
        gateway=_Provisioning({"org-a": signed_free_snapshot("org-z")}),
        operation_repository=operations,
        entitlement_service=_Never(),
    )
    assert (await service.recover_once())["failed"] == 1
    assert operations.updates[0][1]["last_error"] == "ValueError"


@pytest.mark.asyncio
async def test_recovery_and_provisioning_do_nothing_outside_db_entitlements(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "disabled", raising=False)
    service = EntitlementProvisioningService(
        gateway=_Provisioning({}),
        operation_repository=_ProvisionOperations([]),
        entitlement_service=None,
    )
    assert await service.recover_once() == {
        "enqueued": 0,
        "claimed": 0,
        "succeeded": 0,
        "failed": 0,
    }
    assert await service.ensure(org_id="org-1", actor_user_id="user-1") is True


class _ProposalOperations:
    def __init__(self, operation: BillingOperation) -> None:
        self.operation = operation
        self.updates: list[dict] = []

    def claim_seat_proposals(self, *, limit: int, lease_seconds: int):
        return [self.operation]

    def update_claimed_seat_proposal(self, operation: BillingOperation, values: dict):
        self.updates.append(values)
        return operation.model_copy(update=values)


class _Owner:
    def __init__(self, owner: str | None) -> None:
        self.owner = owner

    def get_owner_user_id(self, org_id: str) -> str | None:
        return self.owner


class _Quote:
    def __init__(self, answer: dict) -> None:
        self.answer = answer
        self.calls = 0

    async def request(self, method: str, path: str, **kwargs: Any) -> dict:
        self.calls += 1
        return self.answer


def proposal(target: int | None) -> BillingOperation:
    return BillingOperation(
        id="op-1",
        org_id="org-1",
        kind="member_activation",
        status="awaiting_confirmation",
        idempotency_key="member-activation:user-2:3",
        target_seat_quantity=target,
        attempts=1,
        response_payload={"requires_checkout": True},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "owner", "answer"),
    [
        (proposal(None), "owner-1", {"quote_id": "q"}),
        (proposal(3), None, {"quote_id": "q"}),
        (proposal(3), "owner-1", {"requires_confirmation": True}),
    ],
)
async def test_a_proposal_that_cannot_be_quoted_waits_for_a_retry(
    monkeypatch, operation, owner, answer
) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "required")
    operations = _ProposalOperations(operation)
    service = SeatProposalService(
        organization_repository=_Owner(owner),
        gateway=_Quote(answer),
        operation_repository=operations,
    )
    result = await service.recover_once()
    assert result == {"claimed": 1, "quoted": 0, "failed": 1}
    assert operations.updates[-1]["status"] == "awaiting_confirmation"
    assert operations.updates[-1]["last_error"] == "ValueError"


@pytest.mark.asyncio
async def test_no_proposals_are_made_while_seat_billing_is_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SEAT_BILLING_MODE", "disabled")
    quote = _Quote({"quote_id": "q"})
    service = SeatProposalService(
        organization_repository=_Owner("owner-1"),
        gateway=quote,
        operation_repository=_ProposalOperations(proposal(3)),
    )
    assert await service.recover_once() == {"claimed": 0, "quoted": 0, "failed": 0}
    assert quote.calls == 0
