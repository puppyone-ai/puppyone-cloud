"""Billing operations through real PostgREST and PostgreSQL 17.

Every purchase, plan change, seat change and member admission is one row in
``organization_billing_operations``; the Plan page reads its state from there.
These tests run the repository against the real table so the database rules it
relies on are exercised: one row per idempotency key and per quote, a decided
operation is never overwritten, background claims are leased and fenced, and an
operation is confirmed only by the entitlement that carries its quote.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from postgrest.exceptions import APIError

from src.platform.billing.operations import BillingOperationRepository

from .conftest import entitle, person, space

pytestmark = pytest.mark.native


def commercial(repository: BillingOperationRepository, team: dict, **changes):
    values = {
        "org_id": team["org"],
        "kind": "plan_change",
        "idempotency_key": f"plan-change:{uuid4()}",
        "actor_user_id": team["owner"],
        "quote_id": f"quote-{uuid4()}",
        "application_mode": "plan_change",
        "target_plan_id": "enterprise_v2",
        "current_seat_quantity": 1,
        "target_seat_quantity": 1,
        "baseline_source_revision": 1,
        "response_payload": {"status": "submitted"},
        **changes,
    }
    return repository.create_commercial_operation(**values), values


def test_an_operation_is_recorded_once_per_key_and_once_per_quote(pg, postgrest, monkeypatch):
    team = space(pg)
    repository = BillingOperationRepository(postgrest)
    first, values = commercial(repository, team)

    again = repository.create_commercial_operation(**values)
    assert again.id == first.id and again.status == "pending"
    assert repository.get_by_quote_id(team["org"], values["quote_id"]).id == first.id

    # A retry racing the first request: both read "no row", one insert loses.
    real = repository.get_by_key
    reads: list[str] = []

    def get_by_key(org_id, key):
        reads.append(key)
        return None if len(reads) == 1 else real(org_id, key)

    monkeypatch.setattr(repository, "get_by_key", get_by_key)
    raced = repository.create_commercial_operation(**values)
    assert raced.id == first.id and len(reads) == 2
    monkeypatch.setattr(repository, "get_by_key", real)

    with pytest.raises(APIError):
        # The same quote under another key would apply one quote twice.
        repository.create_commercial_operation(
            **{**values, "idempotency_key": f"plan-change:{uuid4()}"}
        )


def test_a_replayed_key_with_other_terms_is_refused(pg, postgrest) -> None:
    team = space(pg)
    repository = BillingOperationRepository(postgrest)
    _, values = commercial(repository, team)
    with pytest.raises(ValueError, match="idempotency payload mismatch"):
        repository.create_commercial_operation(**{**values, "target_seat_quantity": 3})


def test_a_decided_operation_is_never_overwritten(pg, postgrest) -> None:
    team = space(pg)
    repository = BillingOperationRepository(postgrest)
    operation, _ = commercial(repository, team)

    submitted = repository.update_nonterminal(operation.id, {"status": "submitted"})
    assert submitted is not None and submitted.status == "submitted"
    repository.update(operation.id, {"status": "failed", "last_error": "card_declined"})

    assert repository.update_nonterminal(operation.id, {"status": "submitted"}) is None
    current = repository.get_by_id(team["org"], operation.id)
    assert (current.status, current.last_error) == ("failed", "card_declined")


def test_a_space_without_entitlements_is_provisioned_once(pg, postgrest) -> None:
    bare = space(pg)
    entitled = space(pg)
    entitle(pg, entitled["org"], seats=1, revision=1)
    repository = BillingOperationRepository(postgrest)

    repository.enqueue_missing_entitlement_provisioning(limit=1000)
    assert repository.enqueue_missing_entitlement_provisioning(limit=1000) == 0
    queued = repository.get_by_key(bare["org"], "entitlement-provision:v1")
    assert queued is not None and queued.status == "pending"
    assert repository.get_by_key(entitled["org"], "entitlement-provision:v1") is None
    assert repository.enqueue_entitlement_provisioning(org_id=bare["org"]).id == queued.id

    claimed = {op.id: op for op in repository.claim_entitlement_provisioning(limit=100)}
    assert claimed[queued.id].status == "submitted" and claimed[queued.id].attempts == 1
    again = {op.id for op in repository.claim_entitlement_provisioning(limit=100)}
    assert queued.id not in again, "a claimed provisioning is leased to one worker"


def test_a_seat_proposal_claim_is_fenced_against_a_newer_owner_change(pg, postgrest) -> None:
    team = space(pg)
    subject = person(pg)
    repository = BillingOperationRepository(postgrest)
    proposal = repository.create_or_get(
        {
            "org_id": team["org"],
            "kind": "member_activation",
            "status": "pending",
            "idempotency_key": f"member-activation:{subject}",
            "subject_user_id": subject,
            "target_seat_quantity": 2,
        }
    )
    quoted = repository.create_or_get(
        {
            "org_id": team["org"],
            "kind": "member_activation",
            "status": "pending",
            "idempotency_key": f"member-activation:{uuid4()}",
            "quote_id": f"quote-{uuid4()}",
        }
    )

    claims = {op.id: op for op in repository.claim_seat_proposals(limit=100)}
    assert proposal.id in claims
    assert quoted.id not in claims, "an operation the owner already quoted is not proposed again"
    claim = claims[proposal.id]
    assert claim.attempts == 1 and claim.updated_at > proposal.updated_at

    # The owner quoted the seat in Desktop after the worker's claim.
    repository.update(proposal.id, {"status": "awaiting_confirmation"})
    assert repository.update_claimed_seat_proposal(claim, {"last_error": "stale"}) is None

    fresh = {op.id: op for op in repository.claim_seat_proposals(limit=100, lease_seconds=10)}
    assert proposal.id not in fresh, "the lease from the first claim still holds"


def test_an_operation_is_confirmed_only_by_the_entitlement_with_its_quote(pg, postgrest) -> None:
    team = space(pg)
    repository = BillingOperationRepository(postgrest)
    quote = f"quote-{uuid4()}"
    entitle(pg, team["org"], seats=1, revision=2, plan="enterprise_v2", quote=quote)
    operation, _ = commercial(repository, team, quote_id=quote)

    confirmed = repository.reconcile_from_entitlement(org_id=team["org"], operation_id=operation.id)
    assert (confirmed.status, confirmed.confirmed_revision) == ("confirmed", 2)
    assert confirmed.completed_at is not None

    other, _ = commercial(repository, team)
    unchanged = repository.reconcile_from_entitlement(org_id=team["org"], operation_id=other.id)
    assert unchanged.status == "pending"

    with pytest.raises(RuntimeError, match="disappeared"):
        repository.reconcile_from_entitlement(org_id=team["org"], operation_id=str(uuid4()))


def test_a_spaces_operations_are_listed_newest_first_and_open_ones_counted(pg, postgrest) -> None:
    team = space(pg)
    repository = BillingOperationRepository(postgrest)
    operations = [commercial(repository, team)[0] for _ in range(3)]
    repository.update(operations[0].id, {"status": "confirmed"})

    listed = repository.list_for_org(team["org"])
    assert [op.id for op in listed] == [op.id for op in reversed(operations)]
    assert len(repository.list_for_org(team["org"], limit=1)) == 1
    assert repository.count_pending(team["org"]) == 2
    assert repository.get_by_id(space(pg)["org"], operations[1].id) is None, (
        "an operation is read only within its own Space"
    )


def test_member_activation_is_reserved_through_the_repository(pg, postgrest) -> None:
    team = space(pg)
    entitle(pg, team["org"], seats=2, revision=1)
    subject = person(pg)
    repository = BillingOperationRepository(postgrest)

    reserved = repository.reserve_member_activation(
        org_id=team["org"],
        subject_user_id=subject,
        actor_user_id=team["owner"],
        invitation_id=None,
        role="member",
        idempotency_key=f"member-activation:{subject}",
    )
    assert reserved.kind == "member_activation"
    assert reserved.status == "confirmed"
