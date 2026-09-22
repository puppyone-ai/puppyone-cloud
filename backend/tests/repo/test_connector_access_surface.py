from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.exceptions import BusinessException
from src.repo.connector_service import ConnectorService
from src.repo.models import Connector
from src.platform.repository_target.models import ScopeTarget


NOW = datetime(2026, 5, 31, tzinfo=timezone.utc)


def _connector(
    provider: str,
    *,
    trigger: dict | None = None,
    connector_id: str | None = None,
) -> Connector:
    return Connector(
        id=connector_id or f"c-{provider}",
        target=ScopeTarget(project_id="project-1", scope_id="scope-1"),
        provider=provider,
        name=provider.title(),
        direction=(
            "bidirectional"
            if provider in {"git_remote", "cli", "agent"}
            else "inbound"
        ),
        config={},
        policy={},
        oauth_connection_id=None,
        trigger=trigger or {"type": "manual"},
        status="active",
        last_run_at=None,
        last_run_id=None,
        error_message=None,
        created_by="user-1",
        created_at=NOW,
        updated_at=NOW,
    )


class _FakeConnectorRepository:
    def __init__(self, items: list[Connector] | None = None) -> None:
        self.items = items or []

    def list_by_project(
        self,
        project_id: str,
        *,
        scope_id: str | None = None,
        provider: str | None = None,
        direction: str | None = None,
    ) -> list[Connector]:
        items = [item for item in self.items if item.project_id == project_id]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        if provider:
            items = [item for item in items if item.provider == provider]
        if direction:
            items = [item for item in items if item.direction == direction]
        return items

    def insert(self, **_: object) -> Connector:
        raise AssertionError("import-only connector creation should fail before insert")

    def get(self, connector_id: str) -> Connector | None:
        for item in self.items:
            if item.id == connector_id:
                return item
        return None

    def update(self, connector_id: str, patch: dict) -> Connector | None:
        item = self.get(connector_id)
        if item is None:
            return None
        for key, value in patch.items():
            setattr(item, key, value)
        return item

    def delete(self, connector_id: str) -> None:
        self.items = [item for item in self.items if item.id != connector_id]


def test_list_defaults_to_access_surface_connectors() -> None:
    service = ConnectorService(
        repository=_FakeConnectorRepository([
            _connector("git_remote"),
            _connector("cli"),
            _connector("filesystem"),
            _connector("notion", trigger={"type": "manual"}),
            _connector("github", trigger={"type": "import_once"}),
            _connector("url", trigger={"type": "import_once"}),
        ]),
    )

    visible = service.list("project-1")
    assert [item.provider for item in visible] == [
        "git_remote",
        "cli",
        "notion",
    ]


def test_list_can_include_legacy_import_rows_for_migrations() -> None:
    service = ConnectorService(
        repository=_FakeConnectorRepository([
            _connector("cli"),
            _connector("github", trigger={"type": "import_once"}),
            _connector("url", trigger={"type": "import_once"}),
        ]),
    )

    all_rows = service.list("project-1", access_surface_only=False)
    assert [item.provider for item in all_rows] == ["cli", "github", "url"]


def test_create_rejects_github_access_connector() -> None:
    service = ConnectorService(repository=_FakeConnectorRepository())

    with pytest.raises(BusinessException, match="One-time imports are not Access connectors"):
        service.create(
            project_id="project-1",
            target=ScopeTarget(project_id="project-1", scope_id="scope-1"),
            provider="github",
            direction="inbound",
            name="GitHub",
            config={},
            policy={},
            oauth_connection_id=123,
            trigger={"type": "manual"},
            created_by="user-1",
        )


def test_create_rejects_git_remote_connector() -> None:
    service = ConnectorService(repository=_FakeConnectorRepository())

    with pytest.raises(BusinessException, match="created per repository target"):
        service.create(
            project_id="project-1",
            target=ScopeTarget(project_id="project-1", scope_id="scope-1"),
            provider="git_remote",
            direction="bidirectional",
            name="Git Remote",
            config={},
            policy={},
            oauth_connection_id=None,
            trigger={"type": "manual"},
            created_by="user-1",
        )


def test_create_rejects_import_once_connector() -> None:
    service = ConnectorService(repository=_FakeConnectorRepository())

    with pytest.raises(BusinessException, match="One-time imports are not Access connectors"):
        service.create(
            project_id="project-1",
            target=ScopeTarget(project_id="project-1", scope_id="scope-1"),
            provider="url",
            direction="inbound",
            name="Imported URL",
            config={"source_url": "https://example.com"},
            policy={},
            oauth_connection_id=None,
            trigger={"type": "import_once"},
            created_by="user-1",
        )


def test_delete_rejects_git_remote_builtin_surface() -> None:
    service = ConnectorService(
        repository=_FakeConnectorRepository([
            _connector("git_remote", connector_id="surface-git"),
        ]),
    )

    with pytest.raises(BusinessException, match="Standard Access surfaces"):
        service.delete("surface-git")
