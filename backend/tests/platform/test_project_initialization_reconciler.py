from __future__ import annotations

import pytest

from src.platform.project.initialization_reconciler import (
    ProjectInitializationReconciler,
)


class RepositoryStub:
    def __init__(self, operations):
        self.operations = list(operations)
        self.claim_calls: list[dict] = []
        self.complete_calls: list[dict] = []
        self.abort_calls: list[dict] = []
        self.abandon_calls: list[dict] = []
        self.dead_letter_calls: list[dict] = []
        self.fail_calls: list[dict] = []
        self.abandon_outcome = {"outcome": "accepted"}
        self.abandon_error: Exception | None = None
        self.abort_error: Exception | None = None

    def claim_initializations(self, **kwargs):
        self.claim_calls.append(kwargs)
        claimed, self.operations = self.operations[: kwargs["limit"]], self.operations[
            kwargs["limit"] :
        ]
        return claimed

    def complete_initialization(self, **kwargs):
        self.complete_calls.append(kwargs)
        return {"outcome": "completed"}

    def fail_initialization(self, **kwargs):
        self.fail_calls.append(kwargs)
        return True

    def abort_deferred_publication(self, **kwargs):
        self.abort_calls.append(kwargs)
        if self.abort_error:
            raise self.abort_error
        return {"outcome": "accepted"}

    def abandon_initialization(self, **kwargs):
        self.abandon_calls.append(kwargs)
        if self.abandon_error:
            raise self.abandon_error
        return self.abandon_outcome

    def dead_letter_initialization(self, **kwargs):
        self.dead_letter_calls.append(kwargs)
        return True


class VersionEngineStub:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.initialized: list[str] = []

    async def initialize_project_tree(self, project_id: str):
        self.initialized.append(project_id)
        if self.error:
            raise self.error


class FakeWriteLease:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _operation(
    attempts=1,
    publication_mode="empty",
    deadline="2099-01-01T00:00:00+00:00",
):
    return {
        "operation_key": "123e4567-e89b-42d3-a456-426614174000",
        "actor_user_id": "user-1",
        "project_id": "project-1",
        "initialization_attempts": attempts,
        "publication_mode": publication_mode,
        "initialization_deadline_at": deadline,
    }


@pytest.mark.asyncio
async def test_reconciler_resumes_one_operation_through_l5_and_marks_it_ready():
    repository = RepositoryStub([_operation()])
    engine = VersionEngineStub()
    reconciler = ProjectInitializationReconciler(
        repository,
        engine,
        worker_id="worker-1",
        write_lease_factory=FakeWriteLease,
    )

    summary = await reconciler.run_once(lease_seconds=420)

    assert summary.claimed == summary.completed == 1
    assert summary.failed == 0
    assert repository.claim_calls == [
        {"worker_id": "worker-1", "limit": 1, "lease_seconds": 420}
    ]
    assert engine.initialized == ["project-1"]
    assert repository.complete_calls == [
        {
            "project_id": "project-1",
            "operation_key": "123e4567-e89b-42d3-a456-426614174000",
            "actor_user_id": "user-1",
        }
    ]


@pytest.mark.asyncio
async def test_reconciler_aborts_expired_deferred_publication_without_l5_publish():
    repository = RepositoryStub([_operation(publication_mode="deferred")])
    engine = VersionEngineStub()
    reconciler = ProjectInitializationReconciler(
        repository,
        engine,
        worker_id="worker-1",
        write_lease_factory=FakeWriteLease,
    )

    summary = await reconciler.run_once()

    assert summary.claimed == summary.aborted == 1
    assert summary.completed == summary.failed == 0
    assert engine.initialized == []
    assert repository.complete_calls == []
    assert repository.abort_calls == [
        {
            "project_id": "project-1",
            "operation_key": "123e4567-e89b-42d3-a456-426614174000",
            "actor_user_id": "user-1",
            "quiescence_seconds": 3600,
            "worker_id": "worker-1",
        }
    ]


@pytest.mark.asyncio
async def test_reconciler_keeps_failed_operation_durable_for_backoff_retry():
    repository = RepositoryStub([_operation(attempts=2)])
    engine = VersionEngineStub(RuntimeError("temporary object-store failure"))
    reconciler = ProjectInitializationReconciler(
        repository,
        engine,
        worker_id="worker-1",
        write_lease_factory=FakeWriteLease,
    )

    summary = await reconciler.run_once()

    assert summary.claimed == summary.failed == 1
    assert summary.completed == 0
    assert repository.complete_calls == []
    assert repository.fail_calls == [
        {
            "operation_key": "123e4567-e89b-42d3-a456-426614174000",
            "actor_user_id": "user-1",
            "worker_id": "worker-1",
            "error": "temporary object-store failure",
            "retry_after_seconds": 30,
        }
    ]


@pytest.mark.asyncio
async def test_reconciler_abandons_pre_root_project_after_retry_budget_exhaustion():
    repository = RepositoryStub([_operation(attempts=3)])
    engine = VersionEngineStub(RuntimeError("permanent object-store failure"))
    reconciler = ProjectInitializationReconciler(
        repository,
        engine,
        worker_id="worker-1",
        max_attempts=3,
        write_lease_factory=FakeWriteLease,
    )

    summary = await reconciler.run_once()

    assert summary.claimed == summary.aborted == 1
    assert summary.failed == summary.dead_lettered == 0
    assert repository.fail_calls == []
    assert repository.abandon_calls == [
        {
            "project_id": "project-1",
            "operation_key": "123e4567-e89b-42d3-a456-426614174000",
            "actor_user_id": "user-1",
            "quiescence_seconds": 3600,
            "worker_id": "worker-1",
        }
    ]


@pytest.mark.parametrize("abandon_outcome", ["not_abandonable", "forbidden"])
@pytest.mark.asyncio
async def test_reconciler_dead_letters_any_terminal_failure_it_cannot_delete(
    abandon_outcome,
):
    repository = RepositoryStub([_operation(attempts=3)])
    repository.abandon_outcome = {"outcome": abandon_outcome}
    engine = VersionEngineStub(RuntimeError("permanent object-store failure"))
    reconciler = ProjectInitializationReconciler(
        repository,
        engine,
        worker_id="worker-1",
        max_attempts=3,
        write_lease_factory=FakeWriteLease,
    )

    summary = await reconciler.run_once()

    assert summary.claimed == summary.dead_lettered == 1
    assert summary.failed == summary.aborted == 0
    assert repository.fail_calls == []
    assert repository.dead_letter_calls == [
        {
            "operation_key": "123e4567-e89b-42d3-a456-426614174000",
            "actor_user_id": "user-1",
            "worker_id": "worker-1",
            "error": (
                "permanent object-store failure; terminal abandonment outcome: "
                f"{abandon_outcome}"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_reconciler_abandons_expired_empty_operation_without_another_l5_write():
    repository = RepositoryStub(
        [_operation(deadline="2020-01-01T00:00:00+00:00")]
    )
    engine = VersionEngineStub()
    reconciler = ProjectInitializationReconciler(
        repository,
        engine,
        worker_id="worker-1",
        write_lease_factory=FakeWriteLease,
    )

    summary = await reconciler.run_once()

    assert summary.claimed == summary.aborted == 1
    assert engine.initialized == []
    assert repository.fail_calls == []


@pytest.mark.asyncio
async def test_terminal_cleanup_exception_dead_letters_instead_of_retrying_forever():
    repository = RepositoryStub([_operation(attempts=3)])
    repository.abandon_error = RuntimeError("cleanup RPC unavailable")
    engine = VersionEngineStub(RuntimeError("permanent root failure"))
    reconciler = ProjectInitializationReconciler(
        repository,
        engine,
        worker_id="worker-1",
        max_attempts=3,
        write_lease_factory=FakeWriteLease,
    )

    summary = await reconciler.run_once()

    assert summary.dead_lettered == 1
    assert summary.failed == 0
    assert repository.fail_calls == []
    assert "terminal cleanup failed: cleanup RPC unavailable" in (
        repository.dead_letter_calls[0]["error"]
    )


@pytest.mark.asyncio
async def test_deferred_cleanup_exception_dead_letters_after_its_deadline_claim():
    repository = RepositoryStub([_operation(publication_mode="deferred")])
    repository.abort_error = RuntimeError("cleanup RPC unavailable")
    reconciler = ProjectInitializationReconciler(
        repository,
        VersionEngineStub(),
        worker_id="worker-1",
        write_lease_factory=FakeWriteLease,
    )

    summary = await reconciler.run_once()

    assert summary.dead_lettered == 1
    assert summary.failed == 0
    assert repository.fail_calls == []
    assert repository.dead_letter_calls[0]["error"] == (
        "Deferred publication cleanup failed: cleanup RPC unavailable"
    )
