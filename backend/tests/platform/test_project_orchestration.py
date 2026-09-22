from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.exceptions import AppException
from src.platform.project.control_plane import IdempotentProjectResult
from src.platform.project.models import Project
from src.platform.project.orchestration import create_project_with_tree

OPERATION_KEY = "123e4567-e89b-42d3-a456-426614174000"


def _project() -> Project:
    return Project(
        id="project-1",
        name="Copy",
        description=None,
        org_id="org-1",
        created_by="user-1",
        created_at=datetime.now(UTC),
    )


class ControlPlane:
    def __init__(self, *, replayed: bool = False, ready: bool = False) -> None:
        self.replayed = replayed
        self.ready = ready
        self.create_calls: list[dict] = []
        self.complete_calls: list[dict] = []
        self.abort_calls: list[dict] = []

    def create_project(self, **kwargs):
        self.create_calls.append(kwargs)
        return IdempotentProjectResult(
            project=_project(),
            replayed=self.replayed,
            ready=self.ready,
        )

    def complete_project_initialization(self, **kwargs):
        self.complete_calls.append(kwargs)
        return IdempotentProjectResult(
            project=_project(),
            replayed=kwargs["replayed"],
            ready=True,
        )

    def abort_deferred_publication(self, **kwargs):
        self.abort_calls.append(kwargs)


class Engine:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.initialized: list[str] = []

    async def initialize_project_tree(self, project_id: str):
        self.initialized.append(project_id)
        if self.error:
            raise self.error
        return "commit"


class FakeWriteLease:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _kwargs(control_plane, engine, **overrides):
    values = {
        "control_plane": control_plane,
        "version_engine": engine,
        "operation_key": OPERATION_KEY,
        "name": "Copy",
        "description": None,
        "org_id": "org-1",
        "created_by": "user-1",
        "project_limit": 3,
        "publication_mode": "empty",
        "source_fingerprint": {"kind": "empty-git-repository", "version": 1},
        "write_lease_factory": FakeWriteLease,
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_empty_publication_runs_l5_then_publishes_ready() -> None:
    control_plane = ControlPlane()
    engine = Engine()

    result = await create_project_with_tree(**_kwargs(control_plane, engine))

    assert result.ready is True
    assert engine.initialized == ["project-1"]
    assert control_plane.complete_calls == [
        {
            "project_id": "project-1",
            "operation_key": OPERATION_KEY,
            "actor_user_id": "user-1",
            "replayed": False,
        }
    ]
    assert control_plane.create_calls[0]["publication_mode"] == "empty"
    assert control_plane.create_calls[0]["source_fingerprint"] == {
        "kind": "empty-git-repository",
        "version": 1,
    }


@pytest.mark.asyncio
async def test_empty_l5_failure_stays_durable_for_reconciler() -> None:
    control_plane = ControlPlane()
    engine = Engine(RuntimeError("tree failed"))

    with pytest.raises(RuntimeError, match="tree failed"):
        await create_project_with_tree(**_kwargs(control_plane, engine))

    assert control_plane.complete_calls == []
    assert control_plane.abort_calls == []


@pytest.mark.asyncio
async def test_deferred_initializer_failure_aborts_hidden_aggregate() -> None:
    control_plane = ControlPlane()
    engine = Engine()

    async def initialize(_project: Project) -> None:
        raise RuntimeError("content failed")

    with pytest.raises(RuntimeError, match="content failed"):
        await create_project_with_tree(
            **_kwargs(
                control_plane,
                engine,
                publication_mode="deferred",
                source_fingerprint={"kind": "template", "release": "1"},
                initialize=initialize,
            )
        )

    assert control_plane.complete_calls == []
    assert control_plane.abort_calls == [
        {
            "project_id": "project-1",
            "operation_key": OPERATION_KEY,
            "actor_user_id": "user-1",
        }
    ]


@pytest.mark.asyncio
async def test_deferred_replay_does_not_duplicate_non_idempotent_initializer() -> None:
    control_plane = ControlPlane(replayed=True)
    engine = Engine()
    initialized = False

    async def initialize(_project: Project) -> None:
        nonlocal initialized
        initialized = True

    with pytest.raises(AppException) as caught:
        await create_project_with_tree(
            **_kwargs(
                control_plane,
                engine,
                publication_mode="deferred",
                source_fingerprint={"kind": "template", "release": "1"},
                initialize=initialize,
            )
        )

    assert caught.value.status_code == 409
    assert caught.value.details["code"] == "project_publication_in_progress"
    assert initialized is False
    assert engine.initialized == []


@pytest.mark.asyncio
async def test_ready_replay_returns_snapshot_without_reinitializing() -> None:
    control_plane = ControlPlane(replayed=True, ready=True)
    engine = Engine()

    result = await create_project_with_tree(**_kwargs(control_plane, engine))

    assert result.replayed is True
    assert engine.initialized == []
    assert control_plane.complete_calls == []
