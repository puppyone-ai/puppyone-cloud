"""Access and Tools reach the Agent only through ports installed at startup."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.platform.access import surface_kinds
from src.platform.access import router as access_router
from src.platform.access.adapters.agent.config import surface, tool_bindings
from src.tool import service as tool_service
from src.tool import surface_bindings

BACKEND = Path(__file__).resolve().parents[2]


def test_the_api_process_installs_the_agent_when_it_loads():
    probe = (
        "import src.main\n"
        "from src.platform.access.surface_kinds import surface_kind\n"
        "from src.tool.surface_bindings import tool_surface_bindings\n"
        "print(type(surface_kind('agent')).__name__, type(tool_surface_bindings()).__name__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "AgentSurface AgentToolBindings"


def test_an_unregistered_surface_kind_fails_loudly(monkeypatch):
    monkeypatch.setattr(surface_kinds, "_kinds", {})
    with pytest.raises(RuntimeError, match="not registered"):
        surface_kinds.surface_kind("agent")


def test_access_builds_the_response_from_the_registered_surface(monkeypatch):
    created = SimpleNamespace(id="agent-1", name="Helper", mcp_api_key="key")
    surface = SimpleNamespace(create=Mock(return_value=created))
    monkeypatch.setattr(surface_kinds, "_kinds", {"agent": surface})
    payload = SimpleNamespace(project_id="project-1")

    result = access_router._create_agent(payload)

    surface.create.assert_called_once_with(payload)
    assert (result.id, result.kind, result.mcp_api_key) == ("agent-1", "agent", "key")


class Bindings:
    def __init__(self):
        self.tools = {"surface-1": [SimpleNamespace(tool_id="other")]}

    def surface_ids(self, _tool_id):
        return ["surface-1", "surface-2", "surface-1"]

    def tools_on(self, surface_id):
        return self.tools.get(surface_id, [])

    def mcp_surface_id(self, surface_id):
        return surface_id if surface_id == "surface-1" else None


def service():
    return tool_service.ToolService.__new__(tool_service.ToolService)


def test_a_tool_change_invalidates_each_mcp_surface_once(monkeypatch):
    invalidated = []
    monkeypatch.setattr(surface_bindings, "_installed", Bindings())
    monkeypatch.setattr(tool_service, "invalidate_mcp_surface_cache", invalidated.append)

    service()._invalidate_bound_agents_mcp("tool-1")

    assert invalidated == ["surface-1"]


def test_renaming_a_tool_checks_siblings_on_every_bound_surface(monkeypatch):
    checked = []
    monkeypatch.setattr(surface_bindings, "_installed", Bindings())
    subject = service()
    monkeypatch.setattr(
        subject,
        "_check_sibling_name_conflict",
        lambda tool, user, name, surface, tools: checked.append((surface, len(tools))),
    )

    subject._assert_name_update_no_conflict("tool-1", "user-1", "new")

    assert checked == [("surface-1", 1), ("surface-2", 0), ("surface-1", 1)]


@pytest.mark.parametrize(
    "call",
    [
        lambda subject: subject._invalidate_bound_agents_mcp("tool-1"),
        lambda subject: subject._assert_name_update_no_conflict("tool-1", "user-1", "new"),
    ],
    ids=["invalidate", "rename"],
)
def test_missing_bindings_are_not_swallowed_as_best_effort(monkeypatch, call):
    monkeypatch.setattr(surface_bindings, "_installed", None)
    with pytest.raises(RuntimeError, match="not installed"):
        call(service())


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        (SimpleNamespace(id="agent-1", mcp_enabled=True), "agent-1"),
        (SimpleNamespace(id="agent-1", mcp_enabled=False), None),
        (None, None),
    ],
    ids=["exposed", "not-exposed", "not-an-agent"],
)
def test_only_agents_exposed_over_mcp_are_invalidated(monkeypatch, agent, expected):
    repository = Mock()
    repository.get_by_id.return_value = agent
    monkeypatch.setattr(tool_bindings, "AgentRepository", lambda: repository)

    assert tool_bindings.AgentToolBindings().mcp_surface_id("agent-1") == expected


def test_regenerating_an_agent_key_uses_the_callers_client(monkeypatch):
    repository = Mock()
    repository.regenerate_mcp_api_key.return_value = "new-key"
    clients = []

    def build(client):
        clients.append(client)
        return repository

    monkeypatch.setattr(surface, "AgentRepository", build)

    assert surface.AgentSurface().regenerate_key("agent-1", "client") == "new-key"
    assert clients == ["client"]
    repository.regenerate_mcp_api_key.assert_called_once_with("agent-1")
