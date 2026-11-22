"""Runtime metering edges: switched off, half-integrated callers, crashes and lost races.

The main paths (reserve, settle once, retry, cancel) are in
``test_runtime_billing.py``. These cases are the ones a rollout or an outage
reaches: metering switched off or shadowed, a caller without an organization or
run id, a run that times out, a heartbeat that fails, a settlement or a
cancellation that PuppyPay does not answer, and a second worker that loses the
claim.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import src.platform.billing.runtime as runtime
import src.platform.project.repository as project_repository
from src.config import settings
from src.exceptions import AppException
from src.platform.billing.gateway import BillingGatewayError
from src.platform.billing.runtime import (
    RuntimeMeteringService,
    _billing_error_code,
    _runtime_error,
    _source_kind,
    guard_unmetered_hosted_runtime,
)
from tests.platform.billing.runtime_support import _context, _Gateway, _Repository


def service() -> tuple[RuntimeMeteringService, _Repository, _Gateway]:
    repository, gateway = _Repository(), _Gateway()
    return RuntimeMeteringService(repository=repository, gateway=gateway), repository, gateway


def calls(gateway: _Gateway, suffix: str) -> list:
    return [call for call in gateway.calls if call[1].endswith(suffix)]


@pytest.fixture
def required(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")


@pytest.fixture
def shadow(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "shadow")


def projects(monkeypatch, org_id: str | None) -> None:
    """The project table the metering service reads an organization from."""

    class Projects:
        def get_by_id(self, project_id: str):
            assert project_id == "project-1"
            return None if org_id is None else SimpleNamespace(org_id=org_id)

    monkeypatch.setattr(project_repository, "ProjectRepositorySupabase", Projects)


# Metering switched off --------------------------------------------------------


@pytest.mark.asyncio
async def test_switched_off_metering_never_reserves_or_blocks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "disabled")
    metering, repository, gateway = service()

    guard_unmetered_hosted_runtime("remote_workspace.create")
    assert await metering.execute(audit_context={}, operation=_answer) == "done"
    assert await metering.ensure_session(audit_context=_context()) is None
    assert await metering.start_session(audit_context=_context()) is None
    assert await metering.resume_session("run-1", project_id="p", actor_id="u") is None
    await metering.check_session("missing-run")

    assert gateway.calls == [] and repository.rows == {}


async def _answer() -> str:
    return "done"


# A caller that is only half integrated ---------------------------------------


@pytest.mark.asyncio
async def test_the_organization_is_read_from_the_project_when_the_caller_omits_it(
    required, monkeypatch
) -> None:
    projects(monkeypatch, "org-1")
    metering, repository, _ = service()
    context = {**_context(), "org_id": None}

    assert await metering.execute(audit_context=context, operation=_answer) == "done"
    assert repository.get("run-1").org_id == "org-1"
    assert repository.get("run-1").status == "settled"


@pytest.mark.asyncio
async def test_required_metering_refuses_compute_without_an_organization(
    required, monkeypatch
) -> None:
    projects(monkeypatch, None)
    metering, _, gateway = service()
    ran = []

    async def operation() -> None:
        ran.append(True)

    with pytest.raises(AppException) as caught:
        await metering.execute(audit_context={**_context(), "org_id": None}, operation=operation)
    assert caught.value.details == {"code": "runtime_billing_context_missing"}
    with pytest.raises(AppException) as caught:
        await metering.start_session(audit_context={**_context(), "org_id": None})
    assert caught.value.details == {"code": "runtime_billing_context_missing"}
    assert ran == [] and gateway.calls == []


@pytest.mark.asyncio
async def test_shadow_metering_runs_compute_without_an_organization_unmetered(
    shadow, monkeypatch, caplog
) -> None:
    projects(monkeypatch, None)
    metering, repository, gateway = service()
    context = {**_context(), "org_id": None}

    assert await metering.execute(audit_context=context, operation=_answer) == "done"
    assert await metering.start_session(audit_context=context) is None
    assert "runtime_metering_shadow_missing_org" in caplog.text
    assert gateway.calls == [] and repository.rows == {}


@pytest.mark.asyncio
async def test_shadow_metering_names_a_run_the_caller_did_not(shadow, caplog) -> None:
    metering, repository, _ = service()
    context = {**_context(), "run_id": None}

    assert await metering.execute(audit_context=context, operation=_answer) == "done"
    (run,) = repository.rows.values()
    assert run.run_id.startswith("sandbox_endpoint:session-1:")
    assert run.status == "settled"
    assert "runtime_metering_shadow_generated_run_id" in caplog.text


@pytest.mark.asyncio
async def test_a_session_needs_a_stable_run_id(required) -> None:
    metering, _, _ = service()
    with pytest.raises(ValueError, match="stable run_id"):
        await metering.start_session(audit_context={**_context(), "run_id": ""})


@pytest.mark.asyncio
async def test_shadow_session_whose_reservation_failed_runs_unmetered(shadow) -> None:
    metering, repository, gateway = service()
    gateway.reserve_error = BillingGatewayError(503, {"error": {"code": "puppypay_down"}})

    assert await metering.start_session(audit_context=_context()) is None
    assert repository.get("run-1").status == "unmetered"
    assert repository.get("run-1").last_error == "puppypay_down"


@pytest.mark.asyncio
async def test_a_session_cannot_move_to_another_organization(required) -> None:
    metering, _, _ = service()
    run_id = await metering.start_session(audit_context=_context())
    try:
        with pytest.raises(ValueError, match="organization binding changed"):
            await metering.ensure_session(audit_context={**_context(), "org_id": "org-2"})
    finally:
        await metering.detach_session(run_id)


# Runs that end badly ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_past_its_time_limit_stops_and_is_still_settled(required) -> None:
    metering, repository, gateway = service()

    async def forever() -> None:
        await asyncio.sleep(60)

    with pytest.raises(TimeoutError):
        await metering.execute(
            audit_context={**_context(), "maximum_runtime_seconds": 1}, operation=forever
        )
    assert repository.get("run-1").status == "settled"
    assert len(calls(gateway, "/settle")) == 1


@pytest.mark.asyncio
async def test_an_accounting_failure_after_the_run_keeps_the_run_result(
    required, monkeypatch, caplog
) -> None:
    metering, repository, _ = service()
    real_get = repository.get

    async def operation() -> str:
        def broken(run_id: str):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(repository, "get", broken)
        return "result"

    assert await metering.execute(audit_context=_context(), operation=operation) == "result"
    assert "runtime_settlement_unexpected_failure" in caplog.text
    # The running row stays for the recovery worker to settle.
    monkeypatch.setattr(repository, "get", real_get)
    assert repository.get("run-1").status == "running"


@pytest.mark.asyncio
async def test_finishing_a_session_counts_time_from_a_naive_start(required) -> None:
    metering, repository, gateway = service()
    run_id = await metering.start_session(audit_context=_context())
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=120)
    repository.update(run_id, {"started_at": naive})

    assert await metering.finish_session(run_id)
    (settle,) = calls(gateway, "/settle")
    assert 120 <= settle[2]["body"]["runtime_seconds"] <= 125


# A crashed reservation and a second worker -----------------------------------


@pytest.mark.asyncio
async def test_a_stale_pending_reservation_with_a_naive_timestamp_is_retried(required) -> None:
    metering, repository, gateway = service()
    repository.create_or_get(
        {
            "run_id": "run-1",
            "org_id": "org-1",
            "project_id": "project-1",
            "runtime_kind": "sandbox",
            "status": "pending_reservation",
            "idempotency_key": "runtime-reserve:run-1",
            "estimated_units": 5,
        }
    )
    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    repository.rows["run-1"] = repository.rows["run-1"].model_copy(update={"updated_at": old})

    assert await metering.execute(audit_context=_context(), operation=_answer) == "done"
    assert len(calls(gateway, "/runtime/reservations")) == 1


@pytest.mark.asyncio
async def test_a_worker_that_loses_the_retry_claim_reports_the_run_in_progress(
    required, monkeypatch
) -> None:
    metering, repository, gateway = service()
    gateway.reserve_error = BillingGatewayError(503, {"error": {"code": "puppypay_down"}})
    with pytest.raises(AppException):
        await metering.execute(audit_context=_context(), operation=_answer)
    assert repository.get("run-1").status == "reservation_failed"

    def lost(**_):
        # Another worker claimed it between our read and our claim.
        repository.update("run-1", {"status": "pending_reservation"})
        return None

    monkeypatch.setattr(repository, "claim_reservation_retry", lost)
    with pytest.raises(AppException) as caught:
        await metering.execute(audit_context=_context(), operation=_answer)
    assert caught.value.details["code"] == "runtime_run_in_progress"
    assert caught.value.details["status"] == "pending_reservation"


# Heartbeats -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_heartbeat_is_logged_and_the_next_one_extends_the_lease(
    required, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(settings, "RUNTIME_BILLING_HEARTBEAT_SECONDS", 0)
    metering, repository, gateway = service()
    repository.create_or_get(
        {
            "run_id": "run-1",
            "org_id": "org-1",
            "runtime_kind": "sandbox",
            "status": "running",
            "idempotency_key": "runtime-reserve:run-1",
            "reservation_id": "reservation-1",
        }
    )
    real = gateway.request
    beats: list[str] = []

    async def flaky(method, path, **kwargs):
        beats.append(path)
        if len(beats) == 1:
            raise BillingGatewayError(503, {"error": {"code": "puppypay_down"}})
        return await real(method, path, **kwargs)

    monkeypatch.setattr(gateway, "request", flaky)
    task = asyncio.create_task(metering._heartbeat_loop("run-1", "reservation-1"))
    for _ in range(200):
        if repository.get("run-1").expires_at is not None:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(beats) >= 2
    assert repository.get("run-1").expires_at > datetime.now(UTC)
    assert "runtime_heartbeat_failed" in caplog.text


@pytest.mark.asyncio
async def test_a_run_without_a_reservation_sends_no_heartbeat() -> None:
    metering, _, gateway = service()
    await metering._heartbeat_loop("run-1", None)
    assert gateway.calls == []


# Settlement and cancellation guards ------------------------------------------


def reserved(repository: _Repository, status: str = "running", **values) -> None:
    repository.create_or_get(
        {
            "run_id": "run-1",
            "org_id": "org-1",
            "runtime_kind": "sandbox",
            "status": status,
            "idempotency_key": "runtime-reserve:run-1",
            "reservation_id": "reservation-1",
            "started_at": datetime.now(UTC) - timedelta(minutes=2),
            **values,
        }
    )
    repository.update("run-1", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending_reservation", "reservation_failed"])
async def test_a_run_that_never_reserved_is_not_settled_or_cancelled(status) -> None:
    metering, repository, gateway = service()
    reserved(repository, status)
    assert not await metering._settle("run-1", 60, allow_retry=True)
    assert not await metering._cancel("run-1", allow_retry=True)
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_a_retried_run_without_a_fencing_timestamp_is_left_alone(caplog) -> None:
    metering, repository, gateway = service()
    reserved(repository, "failed", metadata={"cancel_requested": True})
    repository.rows["run-1"] = repository.rows["run-1"].model_copy(update={"updated_at": None})

    assert not await metering._settle("run-1", 60, allow_retry=True)
    assert not await metering._cancel("run-1", allow_retry=True)
    assert gateway.calls == []
    # A cancelled run is never settled, so only the cancellation logs.
    assert "runtime_cancellation_missing_fencing_timestamp" in caplog.text

    repository.rows["run-1"] = repository.rows["run-1"].model_copy(
        update={"metadata": {}, "updated_at": None}
    )
    assert not await metering._settle("run-1", 60, allow_retry=True)
    assert "runtime_settlement_missing_fencing_timestamp" in caplog.text


@pytest.mark.asyncio
async def test_a_settling_run_without_a_cancel_request_is_not_cancelled() -> None:
    metering, repository, gateway = service()
    reserved(repository, "settling")
    assert not await metering._cancel("run-1")
    assert not await metering._cancel("run-1", allow_retry=True)
    assert gateway.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["_settle", "_cancel"])
async def test_a_worker_that_loses_the_claim_sends_nothing(action, monkeypatch) -> None:
    metering, repository, gateway = service()
    reserved(repository)
    monkeypatch.setattr(repository, "claim_action", lambda **_: None)
    if action == "_settle":
        assert not await metering._settle("run-1", 60)
    else:
        assert not await metering._cancel("run-1")
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_terminal_runs_are_never_cancelled() -> None:
    metering, repository, gateway = service()
    reserved(repository, "settled")
    assert not await metering._cancel("run-1", allow_retry=True)
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_a_cancellation_puppypay_did_not_answer_is_finished_by_recovery(
    required, monkeypatch, caplog
) -> None:
    metering, repository, gateway = service()
    reserved(repository)
    real = gateway.request

    async def down(method, path, **kwargs):
        if path.endswith("/cancel"):
            raise BillingGatewayError(503, {"error": {"code": "puppypay_down"}})
        return await real(method, path, **kwargs)

    monkeypatch.setattr(gateway, "request", down)
    await metering.cancel_session("run-1")
    run = repository.get("run-1")
    assert (run.status, run.last_error, run.attempts) == ("failed", "puppypay_down", 1)
    assert run.metadata["cancel_requested"] is True
    assert "runtime_cancellation_deferred" in caplog.text

    monkeypatch.setattr(gateway, "request", real)
    repository.recovery_rows = [repository.get("run-1")]
    assert await metering.recover_once() == 1
    run = repository.get("run-1")
    assert (run.status, run.actual_units) == ("canceled", 0)
    assert calls(gateway, "/settle") == [], "a cancelled run is never charged"


# Recovery ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_charges_a_crashed_run_from_its_naive_start_and_skips_bad_rows(
    monkeypatch, caplog
) -> None:
    metering, repository, gateway = service()
    reserved(repository)
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=300)
    repository.update("run-1", {"started_at": naive, "status": "failed"})
    repository.create_or_get(
        {
            "run_id": "no-reservation",
            "org_id": "org-1",
            "runtime_kind": "sandbox",
            "status": "failed",
            "idempotency_key": "runtime-reserve:no-reservation",
        }
    )
    repository.create_or_get(
        {
            "run_id": "broken",
            "org_id": "org-1",
            "runtime_kind": "sandbox",
            "status": "failed",
            "idempotency_key": "runtime-reserve:broken",
            "reservation_id": "reservation-broken",
        }
    )
    real_settle = metering._settle

    async def settle(run_id, seconds, **kwargs):
        if run_id == "broken":
            raise RuntimeError("malformed row")
        return await real_settle(run_id, seconds, **kwargs)

    monkeypatch.setattr(metering, "_settle", settle)
    repository.recovery_rows = [
        repository.get("broken"),
        repository.get("no-reservation"),
        repository.get("run-1"),
    ]

    assert await metering.recover_once() == 1
    (settled,) = calls(gateway, "/settle")
    assert 300 <= settled[2]["body"]["runtime_seconds"] <= 305
    assert repository.get("no-reservation").status == "failed"
    assert "runtime_recovery_item_failed" in caplog.text


# Small pieces -----------------------------------------------------------------


def test_every_compute_source_maps_to_the_kind_that_pays_for_it() -> None:
    assert _source_kind("connector") == "connector"
    assert _source_kind("workspace") == "workspace"
    assert _source_kind("automation") == "automation"
    assert _source_kind("sandbox_endpoint") == "sandbox"
    assert _source_kind("") == "sandbox"


def test_puppypay_errors_without_an_error_body_still_name_a_code() -> None:
    bare = BillingGatewayError(502, {"detail": "Bad Gateway"})
    assert _billing_error_code(bare) == "runtime_billing_failed"
    error = _runtime_error(bare)
    assert error.status_code == 502
    assert error.details == {"code": "runtime_reservation_denied", "retryable": True}

    denied = _runtime_error(
        BillingGatewayError(
            402, {"error": {"code": "agent_hours_exhausted", "message": "Out of hours"}}
        )
    )
    assert (denied.status_code, denied.message) == (402, "Out of hours")
    assert denied.details == {"code": "agent_hours_exhausted", "retryable": False}


def test_the_service_and_its_repository_are_created_once(monkeypatch) -> None:
    class Gateway:
        pass

    monkeypatch.setattr(runtime, "PuppyPayGateway", Gateway)
    monkeypatch.setattr(runtime, "RuntimeRunRepository", _Repository)
    monkeypatch.setattr(runtime, "_runtime_metering_service", None)

    first = runtime.get_runtime_metering_service()
    assert runtime.get_runtime_metering_service() is first
    assert first._repo is first._repo
    assert isinstance(first._repo, _Repository)
