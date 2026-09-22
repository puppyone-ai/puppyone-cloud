from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.platform.auth.initialization import (
    UserInitializationService,
    _demo_project_operation_key,
)
from src.platform.project.control_plane import IdempotentProjectResult
from src.platform.project.models import Project


def _project() -> Project:
    return Project(
        id="demo-project",
        name="Get Started",
        description="Demo",
        org_id="org-1",
        created_by="user-1",
        created_at=datetime.now(UTC),
    )


def test_demo_operation_identity_is_stable_canonical_uuid4() -> None:
    first = _demo_project_operation_key("user-1", "org-1")
    assert first == _demo_project_operation_key("user-1", "org-1")
    assert first != _demo_project_operation_key("user-2", "org-1")
    assert UUID(first).version == 4
    assert str(UUID(first)) == first


@pytest.mark.asyncio
async def test_demo_creation_uses_empty_l5_publication_with_stable_identity(
    monkeypatch,
) -> None:
    calls: list[dict] = []
    seeded: list[dict] = []

    async def publish(**kwargs):
        calls.append(kwargs)
        return IdempotentProjectResult(project=_project(), replayed=False, ready=True)

    async def seed(**kwargs):
        seeded.append(kwargs)
        return {"files": ["README.md"]}

    monkeypatch.setattr(
        "src.platform.project.orchestration.create_project_with_tree",
        publish,
    )
    monkeypatch.setattr(
        "src.platform.project.templates.seed_template_content",
        seed,
    )
    monkeypatch.setattr(
        "src.version_engine.bootstrap.dependencies.build_worker_version_engine_container",
        lambda: SimpleNamespace(write_engine=lambda: SimpleNamespace()),
    )
    monkeypatch.setattr(
        "src.platform.entitlements.service.EntitlementService.enforced_limit_value",
        lambda _self, _org, _key: 5,
    )

    service = object.__new__(UserInitializationService)
    service._project_control_plane = SimpleNamespace()
    result = await service._seed_demo_project(user_id="user-1", org_id="org-1")

    assert result == "demo-project"
    assert calls[0]["operation_key"] == _demo_project_operation_key("user-1", "org-1")
    assert calls[0]["publication_mode"] == "empty"
    assert calls[0]["source_fingerprint"] == {
        "kind": "onboarding-demo",
        "template_id": "get-started",
        "version": 1,
    }
    assert seeded == [
        {
            "project_id": "demo-project",
            "template_id": "get-started",
            "created_by": "user-1",
        }
    ]
