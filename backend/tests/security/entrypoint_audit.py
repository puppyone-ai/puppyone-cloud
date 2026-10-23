"""Static review gates, not a Python data-flow or authorization proof.

Scan the whole source tree. Exact call-site budgets require review when a new
module, receiver, helper or additional read is introduced. Dynamic execution
tests must still prove authorization, snapshot ownership and cleanup.
"""

import ast
from collections import Counter
from typing import get_args

FACTORIES = {"build_version_engine_container", "build_worker_version_engine_container"}
READ_METHODS = {
    "read_file", "read_file_range", "list_dir", "list_tree", "stat",
    "get_head_commit_id", "get_root_hash", "get_read_revision", "get_path_timestamps",
    "get_scope_head_commit_id", "get_scope_head_commit_id_for_path",
    "read_file_in_scope", "read_file_range_in_scope", "list_dir_in_scope",
    "list_tree_in_scope", "stat_in_scope",
}
RESOURCE_PARAMS = {
    "project_id", "agent_id", "connection_id", "access_surface_id",
    "synchronize_binding_id", "import_database_source_id", "endpoint_id", "job_id",
    "publish_id", "run_id", "session_id", "snapshot_id", "table_id", "task_id", "tool_id",
}


def source_references(source):
    tree = ast.parse(source)
    aliases = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names if alias.name in FACTORIES
    }
    factories, reads = Counter(), Counter()

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def visit_scope(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_FunctionDef = visit_scope
        visit_AsyncFunctionDef = visit_scope
        visit_ClassDef = visit_scope

        def record(self, counter, member):
            counter[f'{".".join(self.scope) or "<module>"}::{member}'] += 1

        def visit_Name(self, node):
            name = aliases.get(node.id, node.id)
            if isinstance(node.ctx, ast.Load) and name in FACTORIES:
                self.record(factories, name)

        def visit_Attribute(self, node):
            if isinstance(node.ctx, ast.Load):
                if node.attr in FACTORIES:
                    self.record(factories, node.attr)
                if node.attr in READ_METHODS:
                    self.record(reads, ast.unparse(node))
            self.generic_visit(node)

    Visitor().visit(tree)
    return dict(sorted(factories.items())), dict(sorted(reads.items()))


def source_inventory(src):
    factories, reads = {}, {}
    for path in sorted(src.rglob("*.py")):
        found_factories, found_reads = source_references(path.read_text(encoding="utf-8"))
        key = path.relative_to(src).as_posix()
        if found_factories:
            factories[key] = found_factories
        if found_reads:
            reads[key] = found_reads
    return {"factories": factories, "reads": reads}


def _model_contains_resource(annotation, seen):
    if id(annotation) in seen:
        return False
    seen.add(id(annotation))
    fields = getattr(annotation, "model_fields", {})
    return bool(RESOURCE_PARAMS.intersection(fields)) or any(
        _model_contains_resource(field.annotation, seen) for field in fields.values()
    ) or any(_model_contains_resource(arg, seen) for arg in get_args(annotation))


def project_route_keys(routes):
    """Include nested Depends and nested typed bodies; opaque dicts need review."""
    result = set()
    for route in routes:
        root = getattr(route, "dependant", None)
        methods = getattr(route, "methods", None)
        if root is None or not methods:
            continue
        scoped = "{project_id}" in route.path
        pending, seen = [root], set()
        while pending:
            dependency = pending.pop()
            if id(dependency) in seen:
                continue
            seen.add(id(dependency))
            fields = dependency.path_params + dependency.query_params + dependency.body_params
            scoped |= any(
                field.name in RESOURCE_PARAMS
                or _model_contains_resource(field.field_info.annotation, set())
                for field in fields
            )
            pending.extend(dependency.dependencies)
        if scoped:
            result.update((method, route.path) for method in methods - {"HEAD", "OPTIONS"})
    return result
