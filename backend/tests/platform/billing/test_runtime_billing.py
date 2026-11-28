from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.config import settings
from src.exceptions import AppException
from src.platform.billing.gateway import BillingGatewayError
from src.platform.billing.runtime import (
    RuntimeBillingRun,
    RuntimeMeteringService,
    guard_unmetered_hosted_runtime,
)
from tests.platform.billing.runtime_support import _context, _Gateway, _Repository


def test_required_mode_fails_closed_for_unintegrated_compute_surface(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")

    with pytest.raises(AppException) as caught:
        guard_unmetered_hosted_runtime("ingest.ocr_parse")

    assert caught.value.status_code == 503
    assert caught.value.details == {
        "code": "runtime_metering_integration_missing",
        "surface": "ingest.ocr_parse",
    }


def test_shadow_mode_observes_unintegrated_compute_surface_without_blocking(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "shadow")

    guard_unmetered_hosted_runtime("remote_workspace.create")

    assert "runtime_metering_shadow_unintegrated_surface" in caplog.text


@pytest.mark.asyncio
async def test_required_mode_rejects_missing_stable_run_id_before_compute(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    context = _context()
    context.pop("run_id")
    executed = False

    async def operation():
        nonlocal executed
        executed = True

    with pytest.raises(AppException) as caught:
        await RuntimeMeteringService(gateway=_Gateway(), repository=_Repository()).execute(
            audit_context=context,
            operation=operation,
        )

    assert caught.value.status_code == 503
    assert caught.value.details["code"] == "runtime_billing_run_id_missing"
    assert executed is False


@pytest.mark.asyncio
async def test_required_reservation_denial_prevents_compute(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    gateway = _Gateway(
        BillingGatewayError(
            402,
            {"error": {"code": "runtime_credit_insufficient", "message": "No credit"}},
        )
    )
    executed = False

    async def operation():
        nonlocal executed
        executed = True

    with pytest.raises(AppException) as caught:
        await RuntimeMeteringService(gateway=gateway, repository=repository).execute(
            audit_context=_context(),
            operation=operation,
        )

    assert not executed
    assert caught.value.status_code == 402
    assert repository.rows["run-1"].status == "denied"


@pytest.mark.asyncio
async def test_success_reserves_then_settles_once(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    gateway = _Gateway()

    async def operation():
        return "done"

    result = await RuntimeMeteringService(gateway=gateway, repository=repository).execute(
        audit_context=_context(),
        operation=operation,
    )

    assert result == "done"
    assert [path for _, path, _ in gateway.calls] == [
        "/internal/v1/billing/runtime/reservations",
        "/internal/v1/billing/runtime/reservations/reservation-1/settle",
    ]
    assert repository.rows["run-1"].status == "settled"
    assert repository.rows["run-1"].actual_units == 2


@pytest.mark.asyncio
async def test_run_id_cannot_be_reused_for_different_billing_facts(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    repository.rows["run-1"] = RuntimeBillingRun(
        run_id="run-1",
        org_id="another-org",
        project_id="project-1",
        runtime_kind="sandbox",
        status="reserved",
        idempotency_key="runtime-reserve:run-1",
        reservation_id="reservation-1",
        estimated_units=5,
    )
    gateway = _Gateway()

    with pytest.raises(AppException) as caught:
        await RuntimeMeteringService(gateway=gateway, repository=repository).execute(
            audit_context=_context(),
            operation=lambda: None,
        )

    assert caught.value.status_code == 409
    assert caught.value.details["code"] == "runtime_run_identity_conflict"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_operation_failure_still_settles_and_preserves_original_error(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    gateway = _Gateway()

    async def operation():
        raise ValueError("compute failed")

    with pytest.raises(ValueError, match="compute failed"):
        await RuntimeMeteringService(gateway=gateway, repository=repository).execute(
            audit_context=_context(),
            operation=operation,
        )

    assert repository.rows["run-1"].status == "settled"


@pytest.mark.asyncio
async def test_shadow_reservation_failure_allows_compute(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "shadow")
    repository = _Repository()
    gateway = _Gateway(BillingGatewayError(503, {"error": {"code": "upstream_down"}}))

    async def operation():
        return "shadow-result"

    result = await RuntimeMeteringService(gateway=gateway, repository=repository).execute(
        audit_context=_context(),
        operation=operation,
    )

    assert result == "shadow-result"
    assert repository.rows["run-1"].status == "unmetered"

    with pytest.raises(AppException) as caught:
        await RuntimeMeteringService(gateway=_Gateway(), repository=repository).execute(
            audit_context=_context(),
            operation=operation,
        )
    assert caught.value.details["code"] == "runtime_run_already_terminal"


@pytest.mark.asyncio
async def test_terminal_run_id_cannot_execute_compute_twice(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    repository.rows["run-1"] = RuntimeBillingRun(
        run_id="run-1",
        org_id="org-1",
        project_id="project-1",
        runtime_kind="sandbox",
        status="settled",
        idempotency_key="runtime-reserve:run-1",
        reservation_id="reservation-1",
        estimated_units=5,
    )
    executed = False

    async def operation():
        nonlocal executed
        executed = True

    with pytest.raises(AppException) as caught:
        await RuntimeMeteringService(
            gateway=_Gateway(),
            repository=repository,
        ).execute(audit_context=_context(), operation=operation)

    assert caught.value.status_code == 409
    assert caught.value.details["code"] == "runtime_run_already_terminal"
    assert executed is False


@pytest.mark.asyncio
async def test_concurrent_run_id_cannot_execute_compute_twice(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    repository.rows["run-1"] = RuntimeBillingRun(
        run_id="run-1",
        org_id="org-1",
        project_id="project-1",
        runtime_kind="sandbox",
        status="running",
        idempotency_key="runtime-reserve:run-1",
        reservation_id="reservation-1",
        estimated_units=5,
    )

    with pytest.raises(AppException) as caught:
        await RuntimeMeteringService(
            gateway=_Gateway(),
            repository=repository,
        ).execute(audit_context=_context(), operation=lambda: None)

    assert caught.value.status_code == 409
    assert caught.value.details["code"] == "runtime_run_in_progress"


@pytest.mark.asyncio
async def test_reservation_transport_failure_can_retry_same_run(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    failing = _Gateway(BillingGatewayError(503, {"error": {"code": "upstream_down"}}))

    with pytest.raises(AppException):
        await RuntimeMeteringService(gateway=failing, repository=repository).execute(
            audit_context=_context(),
            operation=lambda: None,
        )

    assert repository.rows["run-1"].status == "reservation_failed"
    executed = False

    async def operation():
        nonlocal executed
        executed = True

    await RuntimeMeteringService(gateway=_Gateway(), repository=repository).execute(
        audit_context=_context(),
        operation=operation,
    )
    assert executed is True
    assert repository.rows["run-1"].status == "settled"


@pytest.mark.asyncio
async def test_stale_pending_reservation_is_claimed_after_crash(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    monkeypatch.setattr(settings, "RUNTIME_RESERVATION_CLAIM_SECONDS", 120)
    repository = _Repository()
    repository.rows["run-1"] = RuntimeBillingRun(
        run_id="run-1",
        org_id="org-1",
        project_id="project-1",
        runtime_kind="sandbox",
        status="pending_reservation",
        idempotency_key="runtime-reserve:run-1",
        estimated_units=5,
        updated_at=datetime.now(UTC) - timedelta(seconds=121),
    )
    executed = False

    async def operation():
        nonlocal executed
        executed = True

    await RuntimeMeteringService(gateway=_Gateway(), repository=repository).execute(
        audit_context=_context(),
        operation=operation,
    )

    assert executed is True
    assert repository.rows["run-1"].status == "settled"


@pytest.mark.asyncio
async def test_fresh_pending_reservation_cannot_be_stolen(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    repository.rows["run-1"] = RuntimeBillingRun(
        run_id="run-1",
        org_id="org-1",
        project_id="project-1",
        runtime_kind="sandbox",
        status="pending_reservation",
        idempotency_key="runtime-reserve:run-1",
        estimated_units=5,
        updated_at=datetime.now(UTC),
    )

    with pytest.raises(AppException) as caught:
        await RuntimeMeteringService(gateway=_Gateway(), repository=repository).execute(
            audit_context=_context(),
            operation=lambda: None,
        )

    assert caught.value.status_code == 409
    assert caught.value.details["code"] == "runtime_run_in_progress"


@pytest.mark.asyncio
async def test_recovery_retries_failed_settlement_idempotently(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    run = RuntimeBillingRun(
        run_id="run-recovery",
        org_id="org-1",
        runtime_kind="sandbox",
        status="failed",
        idempotency_key="runtime-reserve:run-recovery",
        reservation_id="reservation-1",
        started_at=datetime.now(UTC) - timedelta(seconds=61),
        metadata={"runtime_seconds": 61},
        updated_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    repository.rows[run.run_id] = run
    repository.recovery_rows = [run]
    gateway = _Gateway()

    recovered = await RuntimeMeteringService(
        gateway=gateway,
        repository=repository,
    ).recover_once()

    assert recovered == 1
    assert repository.rows[run.run_id].status == "settled"
    settle = gateway.calls[0]
    assert settle[2]["idempotency_key"] == "runtime-settle:run-recovery"
    assert settle[2]["body"]["runtime_seconds"] == 61


@pytest.mark.asyncio
async def test_settlement_retry_reuses_identical_payload(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    run = RuntimeBillingRun(
        run_id="run-recovery",
        org_id="org-1",
        runtime_kind="sandbox",
        status="running",
        idempotency_key="runtime-reserve:run-recovery",
        reservation_id="reservation-1",
        started_at=datetime.now(UTC) - timedelta(seconds=61),
    )
    repository.rows[run.run_id] = run

    class _FailOnceGateway(_Gateway):
        async def request(self, method: str, path: str, **kwargs):
            self.calls.append((method, path, kwargs))
            if path.endswith("/settle") and len(self.calls) == 1:
                raise BillingGatewayError(503, {"error": {"code": "upstream_down"}})
            if path.endswith("/settle"):
                return {"runtime_units": 2}
            raise AssertionError(path)

    gateway = _FailOnceGateway()
    service = RuntimeMeteringService(gateway=gateway, repository=repository)
    await service._settle(run.run_id, 61)
    await service._settle(run.run_id, 61, allow_retry=True)

    assert repository.rows[run.run_id].status == "settled"
    assert gateway.calls[0][2]["body"] == gateway.calls[1][2]["body"]
    assert gateway.calls[0][2]["idempotency_key"] == "runtime-settle:run-recovery"


@pytest.mark.asyncio
async def test_provider_start_compensation_cancels_without_usage(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    gateway = _Gateway()
    service = RuntimeMeteringService(gateway=gateway, repository=repository)

    run_id = await service.start_session(audit_context=_context())
    assert run_id == "run-1"
    assert repository.rows["run-1"].status == "running"

    await service.cancel_session("run-1")

    assert repository.rows["run-1"].status == "canceled"
    assert repository.rows["run-1"].actual_units == 0
    assert gateway.calls[-1][1].endswith("/cancel")
    assert gateway.calls[-1][2]["idempotency_key"] == "runtime-cancel:run-1"


@pytest.mark.asyncio
async def test_cancel_intent_is_durable_before_transport(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    run = RuntimeBillingRun(
        run_id="run-cancel",
        org_id="org-1",
        runtime_kind="sandbox",
        status="running",
        idempotency_key="runtime-reserve:run-cancel",
        reservation_id="reservation-1",
    )
    repository.rows[run.run_id] = run

    class _CrashingGateway(_Gateway):
        async def request(self, method: str, path: str, **kwargs):
            raise RuntimeError("process interrupted after durable intent")

    with pytest.raises(RuntimeError, match="process interrupted"):
        await RuntimeMeteringService(
            gateway=_CrashingGateway(),
            repository=repository,
        ).cancel_session(run.run_id)

    persisted = repository.rows[run.run_id]
    assert persisted.status == "settling"
    assert persisted.metadata["cancel_requested"] is True


@pytest.mark.asyncio
async def test_recovery_preserves_cancel_intent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    run = RuntimeBillingRun(
        run_id="run-cancel-recovery",
        org_id="org-1",
        runtime_kind="sandbox",
        status="failed",
        idempotency_key="runtime-reserve:run-cancel-recovery",
        reservation_id="reservation-1",
        metadata={"cancel_requested": True},
        updated_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    repository.rows[run.run_id] = run
    repository.recovery_rows = [run]
    gateway = _Gateway()

    recovered = await RuntimeMeteringService(
        gateway=gateway,
        repository=repository,
    ).recover_once()

    assert recovered == 1
    assert repository.rows[run.run_id].status == "canceled"
    assert gateway.calls[0][1].endswith("/cancel")


@pytest.mark.asyncio
async def test_concurrent_settlement_has_one_fenced_transport_call(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository = _Repository()
    run = RuntimeBillingRun(
        run_id="run-concurrent-settle",
        org_id="org-1",
        runtime_kind="sandbox",
        status="running",
        idempotency_key="runtime-reserve:run-concurrent-settle",
        reservation_id="reservation-1",
        updated_at=datetime.now(UTC),
    )
    repository.rows[run.run_id] = run
    entered = asyncio.Event()
    release = asyncio.Event()

    class _BlockingGateway(_Gateway):
        async def request(self, method: str, path: str, **kwargs):
            self.calls.append((method, path, kwargs))
            entered.set()
            await release.wait()
            return {"runtime_units": 1}

    gateway = _BlockingGateway()
    service = RuntimeMeteringService(gateway=gateway, repository=repository)
    first = asyncio.create_task(service._settle(run.run_id, 1))
    await entered.wait()
    second = await service._settle(run.run_id, 1)
    release.set()
    assert await first is True

    assert second is False
    assert len(gateway.calls) == 1
    assert repository.rows[run.run_id].status == "settled"


@pytest.mark.asyncio
async def test_agent_takeover_reuses_reservation_and_checks_expiry(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository, gateway = _Repository(), _Gateway()
    first = RuntimeMeteringService(repository=repository, gateway=gateway)
    run_id = await first.start_session(audit_context=_context())
    started = repository.get(run_id).started_at
    await first.detach_session(run_id)
    second = RuntimeMeteringService(repository=repository, gateway=gateway)
    try:
        assert (
            await second.resume_session(run_id, project_id="project-1", actor_id="user-1") == run_id
        )
        assert repository.get(run_id).started_at == started
        assert (
            len([call for call in gateway.calls if call[1].endswith("/runtime/reservations")]) == 1
        )
        await second.check_session(run_id)
        with pytest.raises(ValueError):
            await second.resume_session(run_id, project_id="project-1", actor_id="other-user")
        repository.update(run_id, {"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
        with pytest.raises(ValueError):
            await second.check_session(run_id)
        await second.finish_session(run_id)
        await second.finish_session(run_id)
        assert len([call for call in gateway.calls if call[1].endswith("/settle")]) == 1
    finally:
        await second.detach_session(run_id)


@pytest.mark.asyncio
async def test_workspace_epoch_retries_share_one_meter_and_closed_epoch_cannot_restart(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository, gateway = _Repository(), _Gateway()
    service = RuntimeMeteringService(repository=repository, gateway=gateway)
    context = {**_context(), "run_id": "cloud-workbench:owner:generation:1"}
    identity = await service.ensure_session(audit_context=context)
    started = repository.get(identity).started_at
    await service.detach_session(identity)
    try:
        assert await service.ensure_session(audit_context=context) == identity
        assert repository.get(identity).started_at == started
        assert (
            len([call for call in gateway.calls if call[1].endswith("/runtime/reservations")]) == 1
        )
        with pytest.raises(ValueError):
            await service.ensure_session(audit_context={**context, "user_id": "other"})
        assert await service.finish_session(identity, retry=True)
        with pytest.raises(AppException):
            await service.ensure_session(audit_context=context)
    finally:
        await service.detach_session(identity)


@pytest.mark.asyncio
async def test_workspace_stop_does_not_ack_an_unconfirmed_settlement(monkeypatch):
    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    repository, gateway = _Repository(), _Gateway()
    service = RuntimeMeteringService(repository=repository, gateway=gateway)
    identity = await service.ensure_session(audit_context=_context())
    original = gateway.request

    async def fail_settle(method, path, **kwargs):
        if path.endswith("/settle"):
            raise BillingGatewayError(503, {"error": {"code": "fixture_unavailable"}})
        return await original(method, path, **kwargs)

    monkeypatch.setattr(gateway, "request", fail_settle)
    try:
        assert not await service.finish_session(identity, retry=True)
        original_seconds = repository.get(identity).metadata["runtime_seconds"]
        monkeypatch.setattr(gateway, "request", original)
        assert await service.finish_session(identity, retry=True)
        assert repository.get(identity).metadata["runtime_seconds"] == original_seconds
    finally:
        await service.detach_session(identity)


def test_only_agent_work_is_metered_as_an_agent_runtime_kind() -> None:
    from src.platform.billing.runtime import _source_kind
    from src.platform.workspace.lifecycle import meter_max_units, meter_source

    person = {"is_default": True, "agent_registration_id": None}
    external_agent = {"is_default": False, "agent_registration_id": "registration-1"}
    automation = {"is_default": False, "agent_registration_id": None}

    assert _source_kind(meter_source(person)) == "workspace"
    assert _source_kind(meter_source(external_agent)) == "agent"
    assert _source_kind(meter_source(automation)) == "automation"
    assert _source_kind("chat_agent") == "agent"
    assert _source_kind("schedule_agent") == "automation"
    assert meter_max_units(person) == settings.RUNTIME_WORKSPACE_MAX_UNITS
    assert meter_max_units(external_agent) == settings.RUNTIME_AGENT_MAX_UNITS
    assert meter_max_units(automation) == settings.RUNTIME_AGENT_MAX_UNITS


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shadow", "required"])
async def test_workspace_pause_seals_locally_and_never_deadlocks_on_puppypay_outage(
    monkeypatch, caplog, mode
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from src.platform.workspace.lifecycle import WorkbenchLifecycle, meter_id

    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", mode)
    repository, gateway = _Repository(), _Gateway()
    billing = RuntimeMeteringService(repository=repository, gateway=gateway)

    row = {
        "id": "wb-1",
        "project_id": "project-1",
        "user_id": "user-1",
        "generation": "gen-1",
        "compute_epoch": 7,
        "state": "pausing",
        "is_default": True,
        "agent_registration_id": None,
        "resource": {"provider": "fly"},
    }
    run_id = meter_id(row)
    await billing.ensure_session(
        audit_context={
            **_context(),
            "run_id": run_id,
            "source": "workspace",
        }
    )
    repository.update(run_id, {"started_at": datetime.now(UTC) - timedelta(minutes=12)})

    events: list[str] = []
    real_seal = billing.seal_session
    real_request = gateway.request

    async def tracked_seal(identity: str):
        events.append("seal")
        return await real_seal(identity)

    async def puppypay_outage(method, path, **kwargs):
        if path.endswith("/settle"):
            events.append("settle_attempt")
            raise BillingGatewayError(503, {"error": {"code": "puppypay_down"}})
        return await real_request(method, path, **kwargs)

    monkeypatch.setattr(billing, "seal_session", tracked_seal)
    monkeypatch.setattr(gateway, "request", puppypay_outage)

    runtime_provider = SimpleNamespace(
        pause_resource=AsyncMock(
            side_effect=lambda _host: (
                events.append("pause_vm")
                or SimpleNamespace(receipt=lambda _res: {"files_retained": True})
            )
        ),
        aclose=AsyncMock(),
    )
    host = SimpleNamespace(resource={"provider": "fly"}, runtime=runtime_provider)
    repo = Mock(
        save_outcome=Mock(),
        transition=Mock(
            side_effect=lambda r, state, resource=None: (
                events.append(f"transition:{state}") or {**r, "state": state}
            )
        ),
        progress=Mock(side_effect=lambda _r, phase: events.append(f"progress:{phase}")),
    )
    service = SimpleNamespace(
        repository=repo,
        transport=Mock(),
        filesystem=Mock(),
        billing=billing,
        channel_factory=Mock(return_value=host),
        providers=Mock(),
        channels=SimpleNamespace(invalidate=AsyncMock()),
        check=AsyncMock(),
        git=SimpleNamespace(snapshot=AsyncMock(return_value={"status": "saved", "tip": "a" * 40})),
    )

    lifecycle = WorkbenchLifecycle(service)
    await lifecycle.pause(row)

    # Exact decoupled order: stop VM -> durable local seal -> control-plane paused -> fast-path settle.
    assert events == ["pause_vm", "seal", "transition:paused", "progress:paused", "settle_attempt"]
    assert "workbench_settlement_deferred_to_outbox" in caplog.text

    # Outbox row is durably sealed with frozen runtime_seconds ready for background recovery.
    sealed_run = repository.get(run_id)
    assert sealed_run.status == "failed"
    assert sealed_run.attempts == 1
    assert sealed_run.metadata["sealed_at"]
    frozen_seconds = sealed_run.metadata["runtime_seconds"]
    assert 720 <= frozen_seconds <= 730

    # Later when PuppyPay comes back, background recovery settles with the EXACT frozen duration.
    monkeypatch.setattr(gateway, "request", real_request)
    repository.recovery_rows = [repository.get(run_id)]
    assert await billing.recover_once() == 1
    settled_run = repository.get(run_id)
    assert settled_run.status == "settled"
    assert "next_retry_at" not in settled_run.metadata
    settle_call = [call for call in gateway.calls if call[1].endswith("/settle")][-1]
    assert settle_call[2]["body"]["runtime_seconds"] == frozen_seconds


@pytest.mark.asyncio
async def test_workspace_pause_refuses_transition_if_local_outbox_seal_fails(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from src.platform.workspace.lifecycle import WorkbenchLifecycle

    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    runtime_provider = SimpleNamespace(
        pause_resource=AsyncMock(
            return_value=SimpleNamespace(receipt=lambda _res: {"files_retained": True})
        ),
        aclose=AsyncMock(),
    )
    host = SimpleNamespace(resource={"provider": "fly"}, runtime=runtime_provider)
    repo = Mock(save_outcome=Mock(), transition=Mock(), progress=Mock())
    service = SimpleNamespace(
        repository=repo,
        transport=Mock(),
        filesystem=Mock(),
        billing=SimpleNamespace(
            seal_session=AsyncMock(side_effect=RuntimeError("postgres unavailable")),
            finish_session=AsyncMock(return_value=True),
        ),
        channel_factory=Mock(return_value=host),
        providers=Mock(),
        channels=SimpleNamespace(invalidate=AsyncMock()),
        check=AsyncMock(),
        git=SimpleNamespace(snapshot=AsyncMock(return_value={"status": "saved", "tip": "a" * 40})),
    )
    row = {
        "id": "wb-1",
        "project_id": "project-1",
        "user_id": "user-1",
        "generation": "gen-1",
        "compute_epoch": 1,
        "state": "pausing",
        "is_default": True,
        "agent_registration_id": None,
        "resource": {"provider": "fly"},
    }
    with pytest.raises(RuntimeError, match="postgres unavailable"):
        await WorkbenchLifecycle(service).pause(row)
    repo.transition.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_destroy_and_recovery_bound_fast_path_settlement_timeout(
    monkeypatch, caplog
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import src.platform.workspace.lifecycle as lifecycle_mod
    from src.platform.workspace.lifecycle import WorkbenchLifecycle

    monkeypatch.setattr(settings, "RUNTIME_METERING_MODE", "required")
    monkeypatch.setattr(lifecycle_mod, "FAST_PATH_SETTLEMENT_TIMEOUT_SECONDS", 0.02)

    async def slow_settle(*_args, **_kwargs):
        await asyncio.sleep(5)
        return True

    seal = AsyncMock()
    runtime_provider = SimpleNamespace(
        destroy_workspace=AsyncMock(),
        resolve=AsyncMock(
            return_value=[{"provider": "fly", "resource_id": "m1", "initialized": True}]
        ),
        pause_resource=AsyncMock(
            return_value=SimpleNamespace(receipt=lambda _res: {"files_retained": True})
        ),
        aclose=AsyncMock(),
    )
    host = SimpleNamespace(
        resource={"provider": "fly", "resource_id": "m1", "initialized": True},
        runtime=runtime_provider,
    )
    repo = Mock(
        transition=Mock(side_effect=lambda r, state, resource=None: {**r, "state": state}),
        progress=Mock(),
    )
    service = SimpleNamespace(
        repository=repo,
        transport=Mock(),
        filesystem=Mock(),
        billing=SimpleNamespace(
            seal_session=seal, finish_session=AsyncMock(side_effect=slow_settle)
        ),
        channel_factory=Mock(return_value=host),
        providers=Mock(),
        channels=SimpleNamespace(invalidate=AsyncMock()),
        check=AsyncMock(),
    )
    row = {
        "id": "wb-2",
        "project_id": "project-1",
        "user_id": "user-1",
        "generation": "gen-1",
        "compute_epoch": 3,
        "state": "retained",
        "is_default": True,
        "agent_registration_id": None,
        "resource": {"provider": "fly", "resource_id": "m1", "initialized": True},
    }
    lc = WorkbenchLifecycle(service)
    await lc._preserve_failed_start(row)
    assert repo.transition.call_args.args[1] == "paused"
    assert repo.progress.call_args.args[1] == "paused"
    assert seal.await_count == 1

    await lc.destroy({**row, "state": "cleanup_pending"})
    assert repo.transition.call_args.args[1] == "retired"
    assert seal.await_count == 2
    assert "workbench_settlement_deferred_to_outbox" in caplog.text
