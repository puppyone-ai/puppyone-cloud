"""Fakes shared by the runtime metering tests: the billing run table and PuppyPay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.platform.billing.gateway import BillingGatewayError
from src.platform.billing.runtime import RuntimeBillingRun


class _Repository:
    def __init__(self) -> None:
        self.rows: dict[str, RuntimeBillingRun] = {}
        self.recovery_rows: list[RuntimeBillingRun] = []

    def get(self, run_id: str):
        return self.rows.get(run_id)

    def create_or_get(self, values):
        run_id = values["run_id"]
        if run_id in self.rows:
            return self.rows[run_id], False
        self.rows[run_id] = RuntimeBillingRun(**values)
        return self.rows[run_id], True

    def update(self, run_id: str, values):
        self.rows[run_id] = RuntimeBillingRun.model_validate(
            {**self.rows[run_id].model_dump(), **values, "updated_at": datetime.now(UTC)}
        )
        return self.rows[run_id]

    def claim_reservation_retry(
        self,
        *,
        run_id: str,
        expected_status: str,
        stale_before: datetime | None = None,
    ):
        run = self.rows[run_id]
        if run.status != expected_status or run.reservation_id is not None:
            return None
        updated_at = run.updated_at
        if updated_at is not None and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)  # PostgreSQL compares as UTC
        if stale_before is not None and (updated_at is None or updated_at > stale_before):
            return None
        return self.update(
            run_id,
            {"status": "pending_reservation", "last_error": None},
        )

    def claim_action(
        self,
        *,
        run_id: str,
        expected_status: str,
        expected_updated_at: datetime | None,
        metadata: dict,
    ):
        run = self.rows[run_id]
        if run.status != expected_status:
            return None
        if expected_updated_at is not None and run.updated_at != expected_updated_at:
            return None
        return self.update(run_id, {"status": "settling", "metadata": metadata})

    def recoverable(self, *, retry_before: datetime, limit: int = 50):
        del retry_before
        return self.recovery_rows[:limit]


class _Gateway:
    def __init__(self, reserve_error: BillingGatewayError | None = None) -> None:
        self.reserve_error = reserve_error
        self.calls: list[tuple[str, str, dict]] = []

    async def request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("/runtime/reservations"):
            if self.reserve_error:
                raise self.reserve_error
            return {
                "reservation_id": "reservation-1",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
        if path.endswith("/settle"):
            return {"runtime_units": 2}
        if path.endswith("/cancel"):
            return {"status": "released", "released_units": 5}
        if path.endswith("/heartbeat"):
            return {"expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat()}
        raise AssertionError(path)


def _context() -> dict:
    return {
        "org_id": "org-1",
        "project_id": "project-1",
        "user_id": "user-1",
        "source": "sandbox_endpoint",
        "session_id": "session-1",
        "run_id": "run-1",
        "maximum_runtime_units": 5,
    }
