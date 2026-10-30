from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.platform.project.control_plane import ProjectControlPlaneRepository, ProjectControlPlaneService

OPERATION = "8f486cc6-566f-4d61-95aa-ea53eec1a81f"


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.values = {}

    def update(self, values):
        self.values = values
        return self

    def eq(self, name, value):
        self.filters.append((name, value))
        return self

    def execute(self):
        matched = [row for row in self.rows if all(row.get(k) == v for k, v in self.filters)]
        for row in matched:
            row.update(self.values)
        return SimpleNamespace(data=[dict(row) for row in matched])


class _Client:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def table(self, name):
        assert name == "projects"
        query = _Query(self.rows)
        self.queries.append(query)
        return query


def _row(**changes):
    return {"id": "project-1", "org_id": "org-1", "created_by": "user-1",
            "lifecycle_status": "initializing", "visibility": "org", **changes}


def _wire(monkeypatch, rows, *, proof=None, lease=True):
    proof = proof if proof is not None else {
        "initialization_operation_key": OPERATION, "initialization_actor": "user-1"
    }
    monkeypatch.setattr("src.platform.project.write_lease.active_project_write_lease",
                        lambda project: SimpleNamespace(initialization_proof=lambda: proof)
                        if lease and project == "project-1" else None)
    client = _Client(rows)
    return ProjectControlPlaneService(ProjectControlPlaneRepository(client)), client


def _set_private(service):
    service.make_initializing_project_private(project_id="project-1", org_id="org-1",
                                             actor_user_id="user-1", operation_key=OPERATION)


def test_private_initialization_updates_the_hidden_project_without_publishing_it(monkeypatch):
    rows = [_row(), _row(id="other-project"), _row(id="ready-project", lifecycle_status="ready")]
    service, _ = _wire(monkeypatch, rows)

    _set_private(service)

    assert rows[0]["visibility"] == "private"
    assert rows[0]["lifecycle_status"] == "initializing"
    assert rows[1]["visibility"] == rows[2]["visibility"] == "org"


@pytest.mark.parametrize("changed", [{"lifecycle_status": "ready"},
                                    {"lifecycle_status": "deleting"},
                                    {"org_id": "other-org"}, {"created_by": "other-user"}])
def test_private_initialization_cannot_update_another_owner_org_or_lifecycle(monkeypatch, changed):
    row = _row(**changed)
    service, _ = _wire(monkeypatch, [row])

    with pytest.raises(RuntimeError, match="private"):
        _set_private(service)

    assert row["visibility"] == "org"


@pytest.mark.parametrize("proof,lease", [({}, True),
    ({"initialization_operation_key": "different", "initialization_actor": "user-1"}, True),
    ({"initialization_operation_key": OPERATION, "initialization_actor": "different"}, True),
    ({}, False)])
def test_private_initialization_requires_the_exact_owned_initializer_lease(monkeypatch, proof, lease):
    row = _row()
    service, client = _wire(monkeypatch, [row], proof=proof, lease=lease)

    with pytest.raises(RuntimeError, match="private"):
        _set_private(service)

    assert row["visibility"] == "org"
    assert client.queries == []
