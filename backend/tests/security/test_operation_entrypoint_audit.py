"""Require review for new factories/read sites and detect hidden HTTP scope."""

import ast
import inspect
import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.params import Depends as DependsParameter
from pydantic import BaseModel

from tests.security.entrypoint_audit import (
    project_route_keys,
    source_inventory,
    source_references,
)

HERE = Path(__file__).resolve().parent


def test_all_factory_and_read_references_match_reviewed_inventory():
    expected = json.loads((HERE / "operation_entrypoint_inventory.json").read_text())
    actual = source_inventory(HERE.parents[1] / "src")
    assert actual == expected["references"], (
        "New or changed read/bootstrap site: review grant binding, read-session ownership, "
        "loop budgets and event-loop blocking. Remove retired inventory entries too. "
        "Known gaps in this inventory are not a claim of completed migration."
    )
    for gap in expected["known_gaps"]:
        assert gap["reason"]
        assert any(
            site.startswith(gap["function"] + "::")
            for site in actual["reads"][gap["path"]]
        ), f"Remove or reclassify resolved gap: {gap}"


def test_synchronize_public_forwarders_supply_operation_dependencies():
    from src.platform.synchronize import public_router, router

    tree = ast.parse(inspect.getsource(public_router))
    checked = 0
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "operations"
        ):
            continue
        operation = getattr(router, node.func.attr)
        dependencies = {
            name for name, parameter in inspect.signature(operation).parameters.items()
            if isinstance(parameter.default, DependsParameter)
        }
        supplied = {keyword.arg for keyword in node.keywords}
        assert dependencies <= supplied, (node.func.attr, dependencies - supplied)
        checked += 1
    assert checked > 0


@pytest.mark.parametrize("source", [
    "from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container as make\n"
    "def query():\n    return make()\n",
    "import src.version_engine.bootstrap.dependencies as bootstrap\n"
    "async def query():\n    return bootstrap.build_worker_version_engine_container()\n",
])
def test_factory_aliases_cannot_evade_review(source):
    factories, _ = source_references(source)
    assert factories == {"query::build_worker_version_engine_container": 1}


def test_scan_discovers_new_directories_and_function_references(tmp_path):
    folder = tmp_path / "new_domain" / "nested"
    folder.mkdir(parents=True)
    (folder / "query.py").write_text(
        "async def query(ops, project, path):\n"
        "    await offload(ops.for_user(project, 'u').read_file, project, path)\n"
        "    return ops.stat(project, path)\n"
    )
    found = source_inventory(tmp_path)["reads"]["new_domain/nested/query.py"]
    assert found == {
        "query::ops.for_user(project, 'u').read_file": 1, "query::ops.stat": 1,
    }


def test_additional_read_or_changed_receiver_cannot_evade_budget():
    before = source_references("def query(read):\n    read.stat('p', 'x')\n")
    assert before != source_references(
        "def query(read):\n    read.stat('p', 'x')\n    read.stat('p', 'y')\n"
    )
    assert before != source_references("def query(ops):\n    ops.stat('p', 'x')\n")


class ProjectTarget(BaseModel):
    project_id: str


class NestedTargets(BaseModel):
    targets: list[ProjectTarget] | None = None


def test_project_scope_hidden_in_nested_dependency_or_body_is_discovered():
    app = FastAPI()

    def identity(project_id: str):
        return project_id

    def wrapper(target=Depends(identity)):
        return target

    @app.get("/hidden")
    def nested(target=Depends(wrapper)):
        return target

    @app.post("/nested-body")
    def nested_body(body: NestedTargets):
        return body

    @app.get("/public")
    def public():
        return {}

    assert project_route_keys(app.routes) == {
        ("GET", "/hidden"), ("POST", "/nested-body"),
    }
