"""PuppyPay's entitlement publication through real PostgREST and PostgreSQL 17.

PuppyPay publishes each Space's plan, seats and limits as a numbered revision.
The Cloud reads paid access only from this projection, so the rules here decide
what a customer can use: a newer revision replaces an older one, a replay is
harmless, a revision never changes its content, and a quote is attached to the
one revision that applied it.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from postgrest.exceptions import APIError

from src.config import settings
from src.platform.billing.facts import RuntimeBillingFactsRepository
from src.platform.billing.operations import BillingOperationRepository
from src.platform.billing.provisioning import EntitlementProvisioningService
from src.platform.billing.runtime import RuntimeRunRepository
from src.platform.entitlements.models import EntitlementUpsert
from src.platform.entitlements.repository import EntitlementRepository
from src.platform.entitlements.service import EntitlementService
from tests.platform.billing.support import signed_free_snapshot

from .conftest import space

pytestmark = pytest.mark.native


def entitlements(seats: int) -> dict:
    granted = signed_free_snapshot()["entitlements"]
    return {**granted, "limits": {**granted["limits"], "seats.purchased": seats}}


def publication(org: str, revision: int, **changes) -> EntitlementUpsert:
    seats = changes.get("seat_quantity", 2)
    values = {
        "org_id": org,
        "schema_version": "1.1",
        "plan_id": "team_v2",
        "status": "active",
        "source": "puppypay",
        "entitlements": entitlements(seats),
        "seat_quantity": seats,
        "catalog_version": "2026-11-09.2",
        "source_revision": revision,
        "effective_at": datetime.now(UTC),
        "payload_hash": f"{revision:064x}",
        "source_event_id": f"event-{uuid4()}",
        **changes,
    }
    return EntitlementUpsert(**values)


def test_a_newer_revision_replaces_the_plan_and_a_replay_changes_nothing(pg, postgrest) -> None:
    org = space(pg)["org"]
    repository = EntitlementRepository(postgrest)
    assert repository.get_by_org_id(org) is None

    first = repository.publish(publication(org, 1))
    assert (first.outcome, first.source_revision) == ("inserted", 1)
    replay = repository.publish(publication(org, 1))
    assert replay.outcome == "idempotent"

    upgrade = repository.publish(
        publication(org, 2, plan_id="enterprise_v2", seat_quantity=5, payload_hash=f"{2:064x}")
    )
    assert upgrade.outcome == "updated"
    current = repository.get_by_org_id(org)
    assert (current.plan_id, current.seat_quantity, current.source_revision) == (
        "enterprise_v2",
        5,
        2,
    )


def test_an_older_revision_is_refused_at_once_and_never_takes_back_a_newer_plan(
    pg, postgrest
) -> None:
    org = space(pg)["org"]
    repository = EntitlementRepository(postgrest)
    repository.publish(publication(org, 1))
    repository.publish(publication(org, 2, plan_id="enterprise_v2", payload_hash=f"{2:064x}"))

    started = time.monotonic()
    with pytest.raises(APIError) as refused:
        repository.publish(publication(org, 1))
    # PostgREST retries SQLSTATE 40001 forever; this answer must not be one.
    assert refused.value.code == "PT409"
    assert time.monotonic() - started < 3
    assert repository.get_by_org_id(org).plan_id == "enterprise_v2"


@pytest.mark.asyncio
async def test_provisioning_retried_after_a_purchase_completes_and_keeps_the_plan(
    pg, postgrest, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "ENTITLEMENTS_MODE", "db")
    org = space(pg)["org"]
    free = signed_free_snapshot(org)
    entitlements = EntitlementService(repository=EntitlementRepository(postgrest))
    entitlements.publish(EntitlementUpsert(**free))
    # The Space bought a plan before provisioning was confirmed.
    EntitlementRepository(postgrest).publish(publication(org, 2, payload_hash=f"{2:064x}"))

    class PuppyPay:
        async def request(self, method, path, **kwargs):
            # The same idempotency key replays the first answer.
            return free

    operations = BillingOperationRepository(postgrest)
    provisioning = EntitlementProvisioningService(
        gateway=PuppyPay(), operation_repository=operations, entitlement_service=entitlements
    )

    assert await provisioning.ensure(org_id=org, actor_user_id=None)
    operation = operations.get_by_key(org, "entitlement-provision:v1")
    assert (operation.status, operation.confirmed_revision) == ("confirmed", 2)
    assert entitlements.published_snapshot(org).plan_id == "team_v2"


def test_a_revision_cannot_change_its_content(pg, postgrest) -> None:
    org = space(pg)["org"]
    repository = EntitlementRepository(postgrest)
    repository.publish(publication(org, 1))
    with pytest.raises(APIError):
        repository.publish(publication(org, 1, seat_quantity=9, payload_hash=f"{99:064x}"))
    assert repository.get_by_org_id(org).seat_quantity == 2


def test_a_quote_is_attached_to_the_revision_that_applied_it(pg, postgrest) -> None:
    org = space(pg)["org"]
    repository = EntitlementRepository(postgrest)
    quote = f"quote-{uuid4()}"

    ack = repository.publish(publication(org, 1, source_quote_id=quote))
    assert ack.snapshot.source_quote_id == quote
    assert repository.publish(publication(org, 1, source_quote_id=quote)).outcome == "idempotent"
    with pytest.raises(APIError, match="quote correlation conflict"):
        repository.publish(publication(org, 1, source_quote_id=f"quote-{uuid4()}"))


def test_plans_are_read_for_many_spaces_at_once(pg, postgrest) -> None:
    paid, free = space(pg)["org"], space(pg)["org"]
    repository = EntitlementRepository(postgrest)
    repository.publish(publication(paid, 1))

    assert repository.plan_ids_by_org([paid, free]) == {paid: "team_v2"}
    assert repository.plan_ids_by_org([]) == {}


def test_billing_facts_count_every_run_that_still_needs_settling(pg, postgrest) -> None:
    org = space(pg)["org"]
    runs = RuntimeRunRepository(postgrest)
    now = datetime.now(UTC)

    def run(status: str, **values) -> None:
        run_id = f"run-{uuid4()}"
        runs.create_or_get(
            {
                "run_id": run_id,
                "org_id": org,
                "runtime_kind": "sandbox",
                "status": status,
                "idempotency_key": f"runtime-reserve:{run_id}",
                **values,
            }
        )

    for status in ("settling", "failed", "reservation_failed"):
        run(status)
    run(
        "running",
        reservation_id=f"r-{uuid4()}",
        expires_at=(now - timedelta(minutes=1)).isoformat(),
    )
    run(
        "running",
        reservation_id=f"r-{uuid4()}",
        expires_at=(now + timedelta(minutes=5)).isoformat(),
    )
    run("pending_reservation")
    run("settled")

    facts = RuntimeBillingFactsRepository(postgrest)
    assert facts.count_orphans(org, now=now + timedelta(seconds=1)) == 4
    # A reservation in flight becomes an orphan once its claim window passes.
    assert facts.count_orphans(org, now=now + timedelta(hours=1)) == 6
