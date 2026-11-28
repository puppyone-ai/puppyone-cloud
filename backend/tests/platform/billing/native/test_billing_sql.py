"""The billing SQL functions on real PostgreSQL.

Seat admission reads the purchased seats only from the PuppyPay projection and
counts seats already promised to people still joining. An applied change is
declared complete only by the entitlement that carries its quote.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from .conftest import entitle, person, space


def reserve(pg, org: str, subject: str, actor: str, key: str | None = None) -> dict:
    return pg.call_rpc(
        "reserve_billable_member_activation",
        org_id=org,
        subject_user_id=subject,
        actor_user_id=actor,
        invitation_id=None,
        role="member",
        idempotency_key=f"member-activation:{subject}:{uuid4()}" if key is None else key,
    )


def test_a_member_beyond_the_paid_seats_waits_for_a_purchase(pg) -> None:
    team = space(pg)
    entitle(pg, team["org"], seats=1, revision=1)
    newcomer = person(pg)

    waiting = reserve(pg, team["org"], newcomer, team["owner"])

    assert waiting["status"] == "awaiting_confirmation"
    assert (waiting["current_seat_quantity"], waiting["target_seat_quantity"]) == (1, 2)
    assert waiting["response_payload"]["requires_checkout"] is True


def test_a_purchased_seat_admits_the_waiting_member_on_retry(pg) -> None:
    team = space(pg)
    entitle(pg, team["org"], seats=1, revision=1)
    newcomer = person(pg)
    first = reserve(pg, team["org"], newcomer, team["owner"])

    entitle(pg, team["org"], seats=2, revision=2)
    admitted = reserve(pg, team["org"], newcomer, team["owner"])

    assert admitted["id"] == first["id"], "the same pending admission is reused"
    assert admitted["status"] == "confirmed"
    assert admitted["confirmed_revision"] == 2


def test_one_free_seat_is_promised_to_one_person_only(pg) -> None:
    team = space(pg)
    entitle(pg, team["org"], seats=2, revision=1)
    first, second = person(pg), person(pg)

    admitted = reserve(pg, team["org"], first, team["owner"])
    waiting = reserve(pg, team["org"], second, team["owner"])

    assert admitted["status"] == "confirmed"
    assert waiting["status"] == "awaiting_confirmation"
    assert waiting["target_seat_quantity"] == 3


def test_seat_admission_needs_an_effective_projection(pg) -> None:
    team = space(pg)
    with pytest.raises(RuntimeError, match="effective entitlement snapshot is required"):
        reserve(pg, team["org"], person(pg), team["owner"])


def test_seat_admission_needs_a_valid_idempotency_key(pg) -> None:
    team = space(pg)
    entitle(pg, team["org"], seats=2, revision=1)
    with pytest.raises(RuntimeError, match="idempotency key is invalid"):
        reserve(pg, team["org"], person(pg), team["owner"], key="")


def test_someone_already_billable_is_admitted_without_a_seat(pg) -> None:
    team = space(pg, members=2)
    entitle(pg, team["org"], seats=2, revision=1)
    admitted = reserve(pg, team["org"], team["members"][1], team["owner"])
    assert admitted["status"] == "confirmed"
    assert admitted["response_payload"]["already_billable"] is True


def operation(pg, org: str, **values) -> str:
    row = {
        "org_id": org,
        "kind": "plan_change",
        "status": "submitted",
        "idempotency_key": f"op-{uuid4()}",
        "target_plan_id": "enterprise_v2",
        "current_seat_quantity": 2,
        "target_seat_quantity": 2,
        "quote_id": f"quote-{uuid4()}",
        "baseline_source_revision": 1,
        **values,
    }
    columns = ",".join(row)
    literals = ",".join(
        "'" + str(v).replace("'", "''") + "'" if isinstance(v, str) else str(v)
        for v in row.values()
    )
    return pg.sql(
        f"INSERT INTO public.organization_billing_operations({columns}) VALUES ({literals}) "
        "RETURNING id;"
    ).splitlines()[0]


def status_of(pg, operation_id: str) -> dict:
    return pg.row(
        "SELECT status, confirmed_revision, completed_at IS NOT NULL AS completed "
        f"FROM public.organization_billing_operations WHERE id = '{operation_id}'"
    )


def test_the_entitlement_carrying_a_quote_completes_its_operation(pg) -> None:
    team = space(pg)
    entitle(pg, team["org"], seats=2, revision=1, plan="team_v2")
    quote = f"quote-{uuid4()}"
    op = operation(pg, team["org"], quote_id=quote)

    entitle(pg, team["org"], seats=2, revision=2, plan="enterprise_v2", quote=quote)

    assert status_of(pg, op) == {"status": "confirmed", "confirmed_revision": 2, "completed": True}


def test_an_entitlement_for_another_quote_or_target_completes_nothing(pg) -> None:
    team = space(pg)
    entitle(pg, team["org"], seats=2, revision=1, plan="team_v2")
    quote = f"quote-{uuid4()}"
    op = operation(pg, team["org"], quote_id=quote)

    entitle(pg, team["org"], seats=2, revision=2, plan="enterprise_v2", quote=f"quote-{uuid4()}")
    entitle(pg, team["org"], seats=3, revision=3, plan="enterprise_v2", quote=quote)

    assert status_of(pg, op)["status"] == "submitted"


def test_reconciling_from_the_current_entitlement_is_explicit(pg) -> None:
    team = space(pg)
    quote = f"quote-{uuid4()}"
    op = operation(pg, team["org"], quote_id=quote, baseline_source_revision=4)
    entitle(pg, team["org"], seats=2, revision=4, plan="enterprise_v2", quote=quote)
    # The baseline was read after this revision; it cannot be its confirmation.
    assert status_of(pg, op)["status"] == "submitted"
    pg.sql(f"SELECT public.reconcile_billing_operation_from_entitlement('{team['org']}', '{op}');")
    assert status_of(pg, op)["status"] == "submitted"

    entitle(pg, team["org"], seats=2, revision=5, plan="enterprise_v2", quote=quote)
    pg.sql(f"SELECT public.reconcile_billing_operation_from_entitlement('{team['org']}', '{op}');")
    assert status_of(pg, op) == {"status": "confirmed", "confirmed_revision": 5, "completed": True}
