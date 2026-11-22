"""The billing tables through real PostgREST and PostgreSQL 17.

The unit tests replace these repositories with dictionaries. Here the same
calls reach the database the hosted service uses, so the parts a dictionary
cannot show are checked: the trigger that moves ``updated_at`` (the fencing
token that keeps two workers from settling one run twice), the filters that
decide which runs recovery may touch, the storage counter's thresholds and
idempotency, and the reconciliation claim lease.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from postgrest.exceptions import APIError

from src.config import settings
from src.platform.billing.runtime import RuntimeMeteringService, RuntimeRunRepository
from src.platform.billing.storage import STORAGE_METRIC, StorageUsageRepository
from tests.platform.billing.runtime_support import _context, _Gateway

from .conftest import space

pytestmark = pytest.mark.native


def run_values(org: str, **values) -> dict:
    run_id = f"run-{uuid4()}"
    return {
        "run_id": run_id,
        "org_id": org,
        "runtime_kind": "sandbox",
        "status": "pending_reservation",
        "idempotency_key": f"runtime-reserve:{run_id}",
        "estimated_units": 5,
        **values,
    }


def reserved(repository: RuntimeRunRepository, org: str, status: str = "running", **values):
    run, _ = repository.create_or_get(
        run_values(org, status=status, reservation_id=f"reservation-{uuid4()}", **values)
    )
    return run


# Runtime billing runs ---------------------------------------------------------


def test_a_run_is_created_once_and_a_racing_insert_returns_the_winner(
    pg, postgrest, monkeypatch
) -> None:
    org = space(pg)["org"]
    repository = RuntimeRunRepository(postgrest)
    values = run_values(org)

    created, is_new = repository.create_or_get(values)
    again, is_new_again = repository.create_or_get(values)
    assert (is_new, is_new_again) == (True, False)
    assert again.run_id == created.run_id and again.created_at == created.created_at

    # Two workers both read "no row"; the second insert hits the primary key.
    real_get = repository.get
    reads: list[str] = []

    def get(run_id: str):
        reads.append(run_id)
        return None if len(reads) == 1 else real_get(run_id)

    monkeypatch.setattr(repository, "get", get)
    raced, raced_is_new = repository.create_or_get(values)
    assert (raced.run_id, raced_is_new) == (created.run_id, False)
    assert len(reads) == 2, "the insert failed and the winner was read back"


def test_an_insert_the_database_refuses_for_another_reason_is_not_hidden(pg, postgrest) -> None:
    org = space(pg)["org"]
    repository = RuntimeRunRepository(postgrest)
    with pytest.raises(APIError):
        repository.create_or_get(run_values(org, runtime_kind="not-a-kind"))


def test_the_database_moves_updated_at_so_only_one_claim_wins(pg, postgrest) -> None:
    org = space(pg)["org"]
    repository = RuntimeRunRepository(postgrest)
    run = reserved(repository, org)
    metadata = {"runtime_seconds": 60}

    def claim(status, updated_at):
        return repository.claim_action(
            run_id=run.run_id,
            expected_status=status,
            expected_updated_at=updated_at,
            metadata=metadata,
        )

    first = claim("running", run.updated_at)
    assert first is not None and first.status == "settling"
    assert first.metadata == metadata
    assert first.updated_at > run.updated_at, "the trigger moves the fencing token"
    assert claim("running", run.updated_at) is None, "the status moved on"

    # A retry of a stuck settlement is fenced by the timestamp it read.
    retry = claim("settling", first.updated_at)
    assert retry is not None and retry.updated_at > first.updated_at
    assert claim("settling", first.updated_at) is None, "a second retrier read a stale token"


def test_a_failed_reservation_is_retried_by_one_worker_and_a_live_one_is_left_alone(
    pg, postgrest
) -> None:
    org = space(pg)["org"]
    repository = RuntimeRunRepository(postgrest)

    failed, _ = repository.create_or_get(run_values(org, status="reservation_failed"))
    repository.update(failed.run_id, {"last_error": "puppypay_down"})
    claimed = repository.claim_reservation_retry(
        run_id=failed.run_id, expected_status="reservation_failed"
    )
    assert claimed is not None
    assert (claimed.status, claimed.last_error) == ("pending_reservation", None)
    assert (
        repository.claim_reservation_retry(
            run_id=failed.run_id, expected_status="reservation_failed"
        )
        is None
    )

    pending, _ = repository.create_or_get(run_values(org))
    recent = datetime.now(UTC) - timedelta(minutes=1)
    assert (
        repository.claim_reservation_retry(
            run_id=pending.run_id, expected_status="pending_reservation", stale_before=recent
        )
        is None
    ), "a reservation still in flight is not taken over"
    later = datetime.now(UTC) + timedelta(seconds=1)
    assert (
        repository.claim_reservation_retry(
            run_id=pending.run_id, expected_status="pending_reservation", stale_before=later
        )
        is not None
    )

    holding = reserved(repository, org, "reservation_failed")
    assert (
        repository.claim_reservation_retry(
            run_id=holding.run_id, expected_status="reservation_failed"
        )
        is None
    ), "a run that already holds a reservation is never reserved again"


def test_recovery_sees_unfinished_settlements_and_expired_leases_only(pg, postgrest) -> None:
    org = space(pg)["org"]
    repository = RuntimeRunRepository(postgrest)
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    runs = {
        "settling": reserved(repository, org, "settling"),
        "failed": reserved(repository, org, "failed"),
        "reserved_expired": reserved(repository, org, "reserved", expires_at=past),
        "running_expired": reserved(repository, org, "running", expires_at=past),
        "running_live": reserved(repository, org, "running", expires_at=future),
        "reserved_live": reserved(repository, org, "reserved", expires_at=future),
        "settled": reserved(repository, org, "settled", expires_at=past),
    }
    names = {run.run_id: name for name, run in runs.items()}

    found = repository.recoverable(retry_before=datetime.now(UTC) + timedelta(seconds=1), limit=200)
    assert {names[run.run_id] for run in found if run.run_id in names} == {
        "settling",
        "failed",
        "reserved_expired",
        "running_expired",
    }

    # A settlement retried moments ago waits for the retry delay.
    too_soon = repository.recoverable(
        retry_before=datetime.now(UTC) - timedelta(minutes=1), limit=200
    )
    assert not {"settling", "failed"} & {names.get(run.run_id) for run in too_soon}

    assert len(repository.recoverable(retry_before=datetime.now(UTC), limit=1)) == 1
    assert len(repository.recoverable(retry_before=datetime.now(UTC), limit=0)) == 1


@pytest.mark.asyncio
async def test_two_workers_finishing_one_session_settle_it_once(pg, postgrest, monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    org = space(pg)["org"]

    class Gateway(_Gateway):
        async def request(self, method, path, **kwargs):
            response = await super().request(method, path, **kwargs)
            if path.endswith("/runtime/reservations"):
                # The reservation id is unique in the table.
                response = {**response, "reservation_id": f"reservation-{uuid4()}"}
            return response

    gateway = Gateway()
    first = RuntimeMeteringService(repository=RuntimeRunRepository(postgrest), gateway=gateway)
    second = RuntimeMeteringService(repository=RuntimeRunRepository(postgrest), gateway=gateway)
    context = {**_context(), "org_id": org, "project_id": None, "run_id": f"session-{uuid4()}"}
    run_id = await first.start_session(audit_context=context)

    await asyncio.gather(first.finish_session(run_id), second.finish_session(run_id))

    settles = [call for call in gateway.calls if call[1].endswith("/settle")]
    assert len(settles) == 1
    run = RuntimeRunRepository(postgrest).get(run_id)
    assert (run.status, run.actual_units) == ("settled", 2)


# Storage usage ----------------------------------------------------------------


def test_storage_usage_moves_through_thresholds_and_a_replay_changes_nothing(pg, postgrest) -> None:
    org = space(pg)["org"]
    repository = StorageUsageRepository(postgrest)
    assert repository.get(org).value == 0 and repository.get(org).metric == STORAGE_METRIC

    def measure(value: int, key: str, limit: int | None = 100) -> dict:
        return repository.reconcile(
            org_id=org,
            value=value,
            limit=limit,
            idempotency_key=f"storage-{key}-{org}",
            source="storage_reconciler",
            metadata={"test": True},
        )

    thresholds = [measure(value, f"m{value}")["threshold_percent"] for value in (79, 80, 95, 100)]
    assert thresholds == [0, 80, 95, 100]
    lower = measure(10, "m10")
    assert (lower["threshold_percent"], lower["threshold_changed"]) == (0, True)
    assert measure(10, "m10") == {"outcome": "idempotent", "value": 10}
    with pytest.raises(APIError, match="usage_idempotency_payload_mismatch"):
        measure(11, "m10")
    assert measure(5, "unlimited", limit=None)["threshold_percent"] == 0

    usage = repository.get(org)
    assert (usage.value, usage.version, usage.threshold_percent) == (5, 6, 0)
    assert usage.full_reconciled_at is not None

    with pytest.raises(APIError, match="invalid absolute usage measurement"):
        measure(-1, "negative")
    with pytest.raises(APIError, match="usage limit must be nonnegative"):
        measure(1, "negative-limit", limit=-1)
    with pytest.raises(APIError, match="idempotency key is required"):
        repository.reconcile(
            org_id=org,
            value=1,
            limit=None,
            idempotency_key="short",
            source="storage_reconciler",
            metadata={},
        )


def test_a_space_is_claimed_for_reconciliation_once_until_its_lease_ends(pg, postgrest) -> None:
    org = space(pg)["org"]
    repository = StorageUsageRepository(postgrest)

    assert org in repository.claim_reconciliation_batch(limit=200, min_age_seconds=60)
    assert org not in repository.claim_reconciliation_batch(limit=200, min_age_seconds=60), (
        "a second worker does not take a Space whose claim is still held"
    )

    repository.reconcile(
        org_id=org,
        value=0,
        limit=None,
        idempotency_key=f"storage-full-{org}",
        source="storage_reconciler",
        metadata={},
    )
    assert org not in repository.claim_reconciliation_batch(limit=200, min_age_seconds=60), (
        "a Space reconciled moments ago waits for its next turn"
    )

    with pytest.raises(APIError, match="invalid storage reconciliation claim parameters"):
        repository.claim_reconciliation_batch(limit=0, min_age_seconds=60)
