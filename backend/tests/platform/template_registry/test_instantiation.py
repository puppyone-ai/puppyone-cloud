from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.exceptions import AppException, ErrorCode, PermissionException
from src.platform.project.control_plane import (
    IdempotentProjectResult,
    ProjectCreationReplay,
)
from src.platform.project.models import Project
from src.platform.template_registry.instantiation import TemplateInstantiationService


def _project() -> Project:
    return Project(
        id="project-1",
        name="Starter copy",
        description="Copied",
        org_id="org-1",
        created_by="user-1",
        created_at=datetime.now(UTC),
    )


def _template_metadata(*, release_id: str = "1.0.0") -> dict[str, object]:
    return {
        "kind": "template-instantiation",
        "template_id": "hello",
        "release_id": release_id,
        "bundle_sha256": "a" * 64,
    }


class _ControlPlane:
    def __init__(
        self,
        events: list[str],
        *,
        replays: list[ProjectCreationReplay | None] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.replays = list(replays or [None])
        self.error = error
        self.calls: list[dict] = []

    def preflight_project_creation(self, **kwargs):
        self.events.append("preflight")
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.replays.pop(0)


class _Registry:
    def __init__(
        self,
        events: list[str],
        *,
        resolved_release_id: str = "1.0.0",
        fail_if_called: bool = False,
    ) -> None:
        self.events = events
        self.resolved_release_id = resolved_release_id
        self.fail_if_called = fail_if_called

    def status(self):
        if self.fail_if_called:
            raise AssertionError("Registry status must not be read during replay")
        self.events.append("status")
        return SimpleNamespace(instantiation_enabled=True, reason=None)

    async def resolve_release(self, *, template_id: str, release_id: str | None):
        if self.fail_if_called:
            raise AssertionError("Registry release must not be resolved during replay")
        self.events.append("resolve")
        assert template_id == "hello"
        return SimpleNamespace(
            template=SimpleNamespace(name="Hello", description="Starter description"),
            release=SimpleNamespace(
                id=self.resolved_release_id,
                bundle_sha256="a" * 64,
            ),
            bundle=SimpleNamespace(files={"README.md": b"hello"}),
        )


class _Entitlements:
    def __init__(self, events: list[str], *, fail_if_called: bool = False) -> None:
        self.events = events
        self.fail_if_called = fail_if_called

    def enforced_limit_value(self, org_id: str, key: str):
        if self.fail_if_called:
            raise AssertionError("capacity must not be read during replay")
        self.events.append("capacity")
        assert (org_id, key) == ("org-1", "projects.max")
        return 3


class _Writes:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.call = None

    async def bulk_write(self, project_id, files, **kwargs):
        self.events.append("write")
        self.call = (project_id, files, kwargs)
        if self.fail:
            raise RuntimeError("write failed")


def _service(
    events: list[str],
    *,
    control_plane: _ControlPlane | None = None,
    registry: _Registry | None = None,
    entitlements: _Entitlements | None = None,
    writes: _Writes | None = None,
) -> TemplateInstantiationService:
    return TemplateInstantiationService(
        registry=registry or _Registry(events),  # type: ignore[arg-type]
        control_plane=control_plane or _ControlPlane(events),  # type: ignore[arg-type]
        entitlements=entitlements or _Entitlements(events),  # type: ignore[arg-type]
        version_engine=SimpleNamespace(),  # type: ignore[arg-type]
        write_commands=writes or _Writes(events),  # type: ignore[arg-type]
    )


async def _instantiate(
    service: TemplateInstantiationService,
    *,
    release_id: str | None = "1.0.0",
    project_name: str | None = "Custom copy",
) -> object:
    return await service.instantiate(
        template_id="hello",
        release_id=release_id,
        project_name=project_name,
        project_description=None,
        org_id="org-1",
        actor_user_id="user-1",
        operation_key="123e4567-e89b-42d3-a456-426614174000",
    )


@pytest.mark.asyncio
async def test_instantiation_preflights_before_registry_and_persists_replay_metadata(
    monkeypatch,
) -> None:
    events: list[str] = []
    control_plane = _ControlPlane(events)
    writes = _Writes(events)

    async def create_project_with_tree(**kwargs):
        events.append("create")
        assert kwargs["name"] == "Custom copy"
        assert kwargs["publication_mode"] == "deferred"
        assert kwargs["request_fingerprint"] == {
            "kind": "template-instantiation-request",
            "version": 1,
            "template_id": "hello",
            "requested_release_id": "1.0.0",
            "project_name": "Custom copy",
            "project_description": None,
            "org_id": "org-1",
        }
        assert kwargs["result_metadata"] == _template_metadata()
        await kwargs["initialize"](_project())
        return IdempotentProjectResult(project=_project(), replayed=False, ready=True)

    monkeypatch.setattr(
        "src.platform.template_registry.instantiation.create_project_with_tree",
        create_project_with_tree,
    )
    service = _service(events, control_plane=control_plane, writes=writes)

    result = await _instantiate(service)

    assert events == ["preflight", "status", "resolve", "capacity", "create", "write"]
    assert result.project.id == "project-1"
    assert writes.call == (
        "project-1",
        {"README.md": b"hello"},
        {"actor": "user-1", "message": "template:hello@1.0.0"},
    )


@pytest.mark.asyncio
async def test_completed_latest_replay_uses_journal_without_registry_or_capacity() -> None:
    events: list[str] = []
    replay = ProjectCreationReplay(
        project=_project(),
        result_metadata=_template_metadata(release_id="1.0.0"),
    )
    service = _service(
        events,
        control_plane=_ControlPlane(events, replays=[replay]),
        registry=_Registry(events, fail_if_called=True),
        entitlements=_Entitlements(events, fail_if_called=True),
    )

    result = await _instantiate(service, release_id=None, project_name=None)

    assert events == ["preflight"]
    assert result.release_id == "1.0.0"
    assert result.replayed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        AppException(
            code=ErrorCode.VERSION_CONFLICT,
            status_code=409,
            message="in progress",
            details={"code": "project_publication_in_progress"},
        ),
        AppException(
            code=ErrorCode.BAD_REQUEST,
            status_code=409,
            message="key reused",
            details={"code": "idempotency_key_reused"},
        ),
        PermissionException("Project access revoked"),
    ],
)
async def test_preflight_failure_never_touches_registry(error: Exception) -> None:
    events: list[str] = []
    service = _service(
        events,
        control_plane=_ControlPlane(events, error=error),
        registry=_Registry(events, fail_if_called=True),
        entitlements=_Entitlements(events, fail_if_called=True),
    )

    with pytest.raises(type(error)):
        await _instantiate(service)

    assert events == ["preflight"]


@pytest.mark.asyncio
async def test_concurrent_latest_winner_response_is_reshaped_from_durable_metadata(
    monkeypatch,
) -> None:
    events: list[str] = []
    replay = ProjectCreationReplay(
        project=_project(),
        result_metadata=_template_metadata(release_id="1.0.0"),
    )
    control_plane = _ControlPlane(events, replays=[None, replay])

    async def create_project_with_tree(**kwargs):
        events.append("create")
        assert kwargs["result_metadata"]["release_id"] == "2.0.0"
        return IdempotentProjectResult(project=_project(), replayed=True, ready=True)

    monkeypatch.setattr(
        "src.platform.template_registry.instantiation.create_project_with_tree",
        create_project_with_tree,
    )
    service = _service(
        events,
        control_plane=control_plane,
        registry=_Registry(events, resolved_release_id="2.0.0"),
    )

    result = await _instantiate(service, release_id=None, project_name=None)

    assert result.release_id == "1.0.0"
    assert result.replayed is True
    assert events == ["preflight", "status", "resolve", "capacity", "create", "preflight"]


@pytest.mark.asyncio
async def test_instantiation_propagates_deferred_initializer_failure(monkeypatch) -> None:
    events: list[str] = []

    async def create_project_with_tree(**kwargs):
        events.append("create")
        await kwargs["initialize"](_project())
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        "src.platform.template_registry.instantiation.create_project_with_tree",
        create_project_with_tree,
    )
    service = _service(events, writes=_Writes(events, fail=True))

    with pytest.raises(RuntimeError, match="write failed"):
        await _instantiate(service, project_name=None)

    assert events == ["preflight", "status", "resolve", "capacity", "create", "write"]
