from __future__ import annotations

from copy import deepcopy

import pytest

from src.exceptions import AppException
from src.platform.project.control_plane import (
    ProjectControlPlaneRepository,
    ProjectControlPlaneService,
)

OPERATION_KEY = "123e4567-e89b-42d3-a456-426614174000"
PROJECT_ROW = {
    "id": "project-1",
    "name": "Local repository",
    "description": "Published from Desktop",
    "org_id": "org-1",
    "visibility": "org",
    "bound_git_branch": "main",
    "created_by": "00000000-0000-4000-8000-000000000001",
    "created_at": "2026-07-16T00:00:00+00:00",
    "updated_at": "2026-07-16T00:00:00+00:00",
    "share_token": "prj_test",
}


class RepositoryStub:
    def __init__(
        self,
        create_outcomes: list[dict] | None = None,
        replay_outcomes: list[dict] | None = None,
    ):
        self.create_outcomes = list(create_outcomes or [])
        self.replay_outcomes = list(replay_outcomes or [])
        self.create_calls: list[dict] = []
        self.replay_calls: list[dict] = []
        self.complete_calls: list[dict] = []
        self.abort_calls: list[dict] = []
        self.abandon_calls: list[dict] = []
        self.delete_calls: list[dict] = []
        self.abandon_outcome: dict = {
            "outcome": "accepted",
            "job": {"id": "job-1", "status": "pending"},
        }
        self.delete_outcome: dict = {
            "outcome": "deleted",
            "job": {"id": "job-1", "status": "pending"},
        }

    def create_project(self, params):
        self.create_calls.append(deepcopy(params))
        return self.create_outcomes.pop(0)

    def get_project_create_replay(self, params):
        self.replay_calls.append(deepcopy(params))
        return self.replay_outcomes.pop(0)

    def abandon_initialization(self, **kwargs):
        self.abandon_calls.append(kwargs)
        return self.abandon_outcome

    def complete_initialization(self, **kwargs):
        self.complete_calls.append(kwargs)
        return {"outcome": "completed", "project": PROJECT_ROW}

    def abort_deferred_publication(self, **kwargs):
        self.abort_calls.append(kwargs)
        return {
            "outcome": "accepted",
            "job": {"id": "job-abort", "status": "pending"},
        }

    def delete_project(self, **kwargs):
        self.delete_calls.append(kwargs)
        return self.delete_outcome


def _create(service: ProjectControlPlaneService):
    return service.create_project(
        operation_key=OPERATION_KEY,
        name="Local repository",
        description="Published from Desktop",
        org_id="org-1",
        actor_user_id="00000000-0000-4000-8000-000000000001",
        publication_mode="empty",
        source_fingerprint={"kind": "empty-git-repository", "version": 1},
        project_limit=3,
    )


def test_create_and_replay_use_one_canonical_payload_and_original_project_snapshot():
    repository = RepositoryStub(
        [
            {"outcome": "initializing_created", "project": PROJECT_ROW},
            {"outcome": "initializing_replayed", "project": PROJECT_ROW},
        ]
    )
    service = ProjectControlPlaneService(repository)

    created = _create(service)
    replayed = _create(service)

    assert created.project == replayed.project
    assert created.replayed is False
    assert replayed.replayed is True
    assert created.ready is False
    assert replayed.ready is False
    first, second = repository.create_calls
    assert first["p_payload_hash"] == second["p_payload_hash"]
    assert first["p_request_hash"] == second["p_request_hash"] == first["p_payload_hash"]
    assert first["p_result_metadata"] == second["p_result_metadata"] == {}
    assert len(first["p_payload_hash"]) == 64
    assert first["p_project_limit"] == second["p_project_limit"] == 3
    assert first["p_publication_mode"] == second["p_publication_mode"] == "empty"
    # A retry may propose fresh generated identifiers, but the transaction
    # ignores them and returns the operation's persisted original snapshot.
    assert first["p_project_id"] != second["p_project_id"]


def test_workflow_source_fingerprint_participates_in_idempotency_hash():
    repository = RepositoryStub(
        [
            {"outcome": "initializing_created", "project": PROJECT_ROW},
            {"outcome": "initializing_created", "project": PROJECT_ROW},
        ]
    )
    service = ProjectControlPlaneService(repository)
    common = {
        "operation_key": OPERATION_KEY,
        "name": "Starter",
        "description": None,
        "org_id": "org-1",
        "actor_user_id": "00000000-0000-4000-8000-000000000001",
        "publication_mode": "deferred",
        "project_limit": 3,
    }

    service.create_project(
        **common,
        source_fingerprint={"kind": "template", "release_id": "1.0.0"},
    )
    service.create_project(
        **common,
        source_fingerprint={"kind": "template", "release_id": "2.0.0"},
    )

    first, second = repository.create_calls
    assert first["p_payload_hash"] != second["p_payload_hash"]


def test_completed_preflight_replays_durable_result_without_create_or_source_lookup():
    repository = RepositoryStub(
        replay_outcomes=[
            {
                "outcome": "replayed",
                "project": PROJECT_ROW,
                "result_metadata": {"release_id": "1.0.0"},
            }
        ]
    )
    service = ProjectControlPlaneService(repository)
    request_fingerprint = {
        "kind": "template-instantiation-request",
        "template_id": "starter",
        "requested_release_id": None,
        "org_id": "org-1",
    }

    replay = service.preflight_project_creation(
        operation_key=OPERATION_KEY,
        actor_user_id="00000000-0000-4000-8000-000000000001",
        request_fingerprint=request_fingerprint,
    )

    assert replay is not None
    assert replay.project.id == "project-1"
    assert replay.result_metadata == {"release_id": "1.0.0"}
    assert repository.create_calls == []
    assert repository.replay_calls[0]["p_operation_key"] == OPERATION_KEY
    assert len(repository.replay_calls[0]["p_request_hash"]) == 64


def test_in_progress_preflight_is_stable_and_never_falls_through_to_mutable_source():
    service = ProjectControlPlaneService(
        RepositoryStub(replay_outcomes=[{"outcome": "initializing"}])
    )

    with pytest.raises(AppException) as caught:
        service.preflight_project_creation(
            operation_key=OPERATION_KEY,
            actor_user_id="00000000-0000-4000-8000-000000000001",
            request_fingerprint={"kind": "template-request", "org_id": "org-1"},
        )

    assert caught.value.status_code == 409
    assert caught.value.details == {"code": "project_publication_in_progress"}


def test_preflight_during_deletion_is_a_stable_gone_response_not_a_500() -> None:
    service = ProjectControlPlaneService(
        RepositoryStub(replay_outcomes=[{"outcome": "lifecycle_conflict"}])
    )

    with pytest.raises(AppException) as caught:
        service.preflight_project_creation(
            operation_key=OPERATION_KEY,
            actor_user_id="00000000-0000-4000-8000-000000000001",
            request_fingerprint={"kind": "template-request", "org_id": "org-1"},
        )

    assert caught.value.status_code == 410
    assert caught.value.details["code"] == "idempotency_target_gone"


@pytest.mark.parametrize(
    ("outcome", "status_code", "code"),
    [
        ("conflict", 409, "idempotency_key_reused"),
        ("gone", 410, "idempotency_target_gone"),
        ("invalid", 422, "idempotency_key_invalid"),
    ],
)
def test_create_translates_idempotency_terminal_outcomes(outcome, status_code, code):
    service = ProjectControlPlaneService(RepositoryStub([{"outcome": outcome}]))

    with pytest.raises(AppException) as caught:
        _create(service)

    assert caught.value.status_code == status_code
    assert caught.value.details["code"] == code


def test_create_translates_capacity_without_bypassing_transaction_result():
    service = ProjectControlPlaneService(
        RepositoryStub(
            [{"outcome": "capacity_exceeded", "current": 3, "maximum": 3}]
        )
    )

    with pytest.raises(AppException) as caught:
        _create(service)

    assert caught.value.status_code == 403
    assert caught.value.details == {
        "code": "entitlement_required",
        "reason": "limit_exceeded",
        "limit": "projects.max",
        "org_id": "org-1",
        "current": 3,
        "maximum": 3,
    }


def test_delete_and_abandon_persist_the_configured_quiescence_window():
    repository = RepositoryStub()
    service = ProjectControlPlaneService(repository, deletion_quiescence_seconds=4200)

    abandoned = service.abandon_initialization(
        project_id="project-1",
        operation_key=OPERATION_KEY,
        actor_user_id="user-1",
    )
    deleted = service.delete_project(project_id="project-2", actor_user_id="user-1")

    assert abandoned.deletion_job_id == "job-1"
    assert deleted.deletion_job_id == "job-1"
    assert repository.abandon_calls == [
        {
            "project_id": "project-1",
            "operation_key": OPERATION_KEY,
            "actor_user_id": "user-1",
            "quiescence_seconds": 4200,
        }
    ]
    assert repository.delete_calls == [
        {
            "project_id": "project-2",
            "actor_user_id": "user-1",
            "quiescence_seconds": 4200,
        }
    ]


def test_initialization_completion_is_a_separate_idempotent_control_plane_step():
    repository = RepositoryStub()
    service = ProjectControlPlaneService(repository)

    result = service.complete_project_initialization(
        project_id="project-1",
        operation_key=OPERATION_KEY,
        actor_user_id="00000000-0000-4000-8000-000000000001",
        replayed=True,
    )

    assert result.ready is True
    assert result.replayed is True
    assert result.project.id == "project-1"
    assert repository.complete_calls == [
        {
            "project_id": "project-1",
            "operation_key": OPERATION_KEY,
            "actor_user_id": "00000000-0000-4000-8000-000000000001",
        }
    ]


def test_deferred_abort_is_a_durable_control_plane_operation():
    repository = RepositoryStub()
    service = ProjectControlPlaneService(repository, deletion_quiescence_seconds=4200)

    result = service.abort_deferred_publication(
        project_id="project-1",
        operation_key=OPERATION_KEY,
        actor_user_id="user-1",
    )

    assert result.deletion_job_id == "job-abort"
    assert repository.abort_calls == [
        {
            "project_id": "project-1",
            "operation_key": OPERATION_KEY,
            "actor_user_id": "user-1",
            "quiescence_seconds": 4200,
            "worker_id": None,
        }
    ]


def test_storage_inventory_rollout_gate_is_a_typed_503_not_database_noise() -> None:
    class Request:
        def execute(self):
            raise RuntimeError("Project storage inventory is incomplete")

    class Client:
        def rpc(self, *_args, **_kwargs):
            return Request()

    repository = ProjectControlPlaneRepository(Client())

    with pytest.raises(AppException) as caught:
        repository.delete_project(
            project_id="project-1",
            actor_user_id="user-1",
            quiescence_seconds=3600,
        )

    assert caught.value.status_code == 503
    assert caught.value.details == {
        "code": "project_storage_inventory_incomplete",
        "retryable": True,
    }
