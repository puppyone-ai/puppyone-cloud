from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.platform.project.readiness import ProjectReadinessService
from src.platform.project.readiness_repository import ProjectReadinessRepository


@dataclass
class Response:
    data: list[dict]


class Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters = {}

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def is_(self, key, value):
        assert value == "null"
        self.filters[key] = None
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        rows = [
            row
            for row in self.client.rows.get(self.table, [])
            if all(row.get(key) == value for key, value in self.filters.items())
        ]
        return Response(rows)


class Client:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return Query(self, name)


def _resolve(
    *,
    root_surface: bool,
    root_head: str = "",
    child_head: str = "",
    accepted_root_git_push: bool = False,
):
    surfaces = (
        [{
            "id": "git-root",
            "project_id": "project-1",
            "scope_id": None,
            "kind": "git_remote",
            "status": "active",
        }]
        if root_surface
        else []
    )
    states = []
    if root_head:
        states.append(
            {"project_id": "project-1", "scope_path": "", "head_commit_id": root_head}
        )
    if child_head:
        states.append(
            {"project_id": "project-1", "scope_path": "docs", "head_commit_id": child_head}
        )
    return ProjectReadinessService(
        ProjectReadinessRepository(
            Client(
            {
                "projects": [{"id": "project-1", "bound_git_branch": "main"}],
                "access_surfaces": surfaces,
                "version_scope_state": states,
                "version_transactions": (
                    [
                        {
                            "id": 1,
                            "project_id": "project-1",
                            "scope_path": "",
                            "source_channel": "access_git",
                            "status": "committed",
                        }
                    ]
                    if accepted_root_git_push
                    else []
                ),
            }
            )
        )
    ).resolve("project-1")


@pytest.mark.parametrize(
    ("surface", "head", "state", "ready"),
    [
        (False, "", "git_not_created", False),
        (True, "", "awaiting_first_push", False),
        (True, "a" * 40, "ready", True),
    ],
)
def test_root_readiness_state_machine(surface, head, state, ready):
    readiness = _resolve(
        root_surface=surface,
        root_head=head,
        accepted_root_git_push=ready,
    )
    assert readiness.git_state == state
    assert readiness.claude_ready is ready


def test_non_root_head_never_unlocks_claude():
    readiness = _resolve(
        root_surface=True,
        child_head="b" * 40,
        accepted_root_git_push=True,
    )
    assert readiness.git_state == "awaiting_first_push"
    assert readiness.claude_ready is False


def test_product_write_head_never_substitutes_for_first_root_git_push():
    readiness = _resolve(root_surface=True, root_head="c" * 40)
    assert readiness.project_head_exists is True
    assert readiness.project_git_push_accepted is False
    assert readiness.git_state == "awaiting_first_push"
    assert "project_git_push_not_accepted" in readiness.blockers


def test_readiness_wire_contract_names_project_root_explicitly():
    payload = _resolve(
        root_surface=True,
        root_head="d" * 40,
        accepted_root_git_push=True,
    ).as_dict()

    assert payload["git"] == {
        "target": {"kind": "project_root", "project_id": "project-1"},
        "surface_exists": True,
        "head_exists": True,
        "push_accepted": True,
        "default_branch": "main",
        "state": "ready",
    }
