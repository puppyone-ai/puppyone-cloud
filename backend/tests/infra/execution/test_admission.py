import asyncio
import time

import pytest

from src.infra.execution.admission import ExecutionCapacityError, ReadAdmission
from src.infra.execution.context import Execution, current_execution
from src.infra.execution.runner import run_owned
from src.version_engine.infrastructure import owned_work


@pytest.mark.asyncio
async def test_admission_bounds_waiters_and_reclaims_cancelled_slots():
    budget = ReadAdmission(running=1, waiting=1, wait_seconds=0.2)

    async def work():
        async with budget.acquire():
            pytest.fail("cancelled waiter started work")

    async with budget.acquire():
        queued = asyncio.create_task(work())
        await asyncio.sleep(0.01)
        with pytest.raises(ExecutionCapacityError):
            async with budget.acquire():
                pass
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
    async with budget.acquire():
        pass
    assert budget._admitted == 0


@pytest.mark.asyncio
async def test_admission_queue_timeout_is_retryable():
    budget = ReadAdmission(running=1, waiting=1, wait_seconds=0.01)
    async with budget.acquire():
        with pytest.raises(ExecutionCapacityError):
            async with budget.acquire():
                pass
    assert budget._admitted == 0


@pytest.mark.asyncio
async def test_expired_execution_never_starts_work_and_compatibility_shares_context():
    assert owned_work.current_execution is current_execution
    token = current_execution.set(Execution(deadline=time.monotonic() - 1))
    try:
        with pytest.raises(TimeoutError):
            await run_owned(lambda: pytest.fail("expired worker started"))
    finally:
        current_execution.reset(token)


@pytest.mark.asyncio
async def test_cleanup_ignores_expired_cancelled_context_and_joins_repeated_cancel():
    import threading

    from src.infra.execution.runner import run_cleanup

    entered, finish, cleaned = threading.Event(), threading.Event(), threading.Event()

    def cleanup():
        assert current_execution.get() is None
        entered.set()
        assert finish.wait(2)
        cleaned.set()

    execution = Execution(deadline=time.monotonic() - 1)
    execution.cancelled.set()
    token = current_execution.set(execution)
    try:
        task = asyncio.create_task(run_cleanup(cleanup))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        task.cancel()
        await asyncio.sleep(0.005)
        task.cancel()
        await asyncio.sleep(0.005)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
    finally:
        finish.set()
        current_execution.reset(token)
