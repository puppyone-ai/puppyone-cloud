"""Architecture rules for the async/blocking boundary.

See docs/architecture/18-concurrency-boundary.md. Async code reaches blocking
work only through ``src.infra.execution.runner`` (one owned, capacity-limited
boundary shared with FastAPI ``def`` routes).
"""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src"
BOUNDARY = SOURCE / "infra" / "execution" / "runner.py"

_THREAD_HELPERS = {"to_thread", "run_in_threadpool", "iterate_in_threadpool", "run_in_executor"}


def _python_files():
    for path in sorted(SOURCE.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _thread_hops(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _THREAD_HELPERS:
            yield node.lineno, ast.unparse(node)
        elif isinstance(node, ast.ImportFrom) and node.module in {
            "asyncio",
            "anyio",
            "anyio.to_thread",
            "starlette.concurrency",
            "fastapi.concurrency",
        }:
            for alias in node.names:
                if alias.name in _THREAD_HELPERS or alias.name == "to_thread":
                    yield node.lineno, f"from {node.module} import {alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "anyio.to_thread":
                    yield node.lineno, "import anyio.to_thread"


def test_blocking_work_enters_only_through_the_runner_boundary():
    violations = [
        f"{path.relative_to(SOURCE.parent)}:{line} {what}"
        for path, tree in _python_files()
        if path != BOUNDARY
        for line, what in _thread_hops(tree)
    ]
    assert violations == [], "use src.infra.execution.runner.run_blocking:\n" + "\n".join(violations)


# Process entry points: the only places that may create or run an event loop.
_ENTRY_POINTS = {
    "infra/queue_cutover.py",
    "platform/workspace/execution/preflight.py",
    "platform/access/adapters/ssh/gateway.py",
    "platform/access/adapters/agent/runtime/worker.py",
}
_LOOP_CALLS = {
    "run",  # asyncio.run
    "new_event_loop",
    "set_event_loop",
    "run_until_complete",
    "run_forever",
    "run_coroutine_threadsafe",
}


def _is_main_guard(node):
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and ast.unparse(node.test).replace("'", '"') == '__name__ == "__main__"'
    )


def _loop_creation(tree):
    """Yield (line, call) outside ``main()`` / ``if __name__ == "__main__"``."""

    def visit(node, allowed):
        for child in ast.iter_child_nodes(node):
            inside = allowed or _is_main_guard(child) or (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "main"
            )
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                owner = ast.unparse(child.func.value)
                is_loop_call = child.func.attr in _LOOP_CALLS and (
                    owner == "asyncio" or child.func.attr != "run"
                )
                if is_loop_call and not inside:
                    yield child.lineno, ast.unparse(child.func)
            yield from visit(child, inside)

    yield from visit(tree, False)


def test_event_loops_are_created_only_in_process_entry_points():
    violations, entry_point_violations = [], []
    for path, tree in _python_files():
        relative = path.relative_to(SOURCE).as_posix()
        found = [f"{relative}:{line} {call}" for line, call in _loop_creation(tree)]
        if relative in _ENTRY_POINTS:
            entry_point_violations.extend(found)
        else:
            violations.extend(found)
    assert violations == [], "\n".join(violations)
    assert entry_point_violations == [], "entry points create loops only in main():\n" + "\n".join(
        entry_point_violations
    )


# Coroutine handlers that do no I/O at all: they must answer even while the
# shared worker-thread pool is saturated.
_PURE_ASYNC_HANDLERS = {
    ("main.py", "live_check"),
    ("main.py", "health_check"),
}
_ROUTE_DECORATORS = (".get", ".post", ".put", ".patch", ".delete", ".head", ".options", ".api_route")


def _own_nodes(function):
    nested = set()
    for node in ast.walk(function):
        if node is not function and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            nested.update(id(inner) for inner in ast.walk(node))
    return [node for node in ast.walk(function) if id(node) not in nested]


def _does_async_work(function: ast.AsyncFunctionDef) -> bool:
    for node in _own_nodes(function):
        if isinstance(node, (ast.AsyncFor, ast.AsyncWith)):
            return True
        if isinstance(node, ast.Await):
            call = node.value
            if not (isinstance(call, ast.Call) and ast.unparse(call.func) == "run_blocking"):
                return True
    # A nested async generator is a streaming response body (SSE, downloads).
    return any(
        isinstance(node, ast.AsyncFunctionDef)
        and any(isinstance(inner, (ast.Yield, ast.YieldFrom)) for inner in ast.walk(node))
        for node in ast.walk(function)
        if node is not function
    )


def _dependency_names(tree):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) in {"Depends", "Security"}
            and node.args
            and isinstance(node.args[0], (ast.Name, ast.Attribute))
        ):
            target = node.args[0]
            yield target.attr if isinstance(target, ast.Attribute) else target.id


def test_blocking_only_routes_and_dependencies_are_plain_functions():
    files = list(_python_files())
    dependencies = {name for _path, tree in files for name in _dependency_names(tree)}
    violations = []
    for path, tree in files:
        relative = path.relative_to(SOURCE).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            is_route = any(
                isinstance(decorator, ast.Call)
                and ast.unparse(decorator.func).endswith(_ROUTE_DECORATORS)
                for decorator in node.decorator_list
            )
            is_dependency = node.name in dependencies and node.col_offset == 0
            if not (is_route or is_dependency) or (relative, node.name) in _PURE_ASYNC_HANDLERS:
                continue
            if not _does_async_work(node):
                violations.append(f"{relative}:{node.lineno} {node.name}")
    assert violations == [], (
        "a handler/dependency that only blocks is a plain def (FastAPI runs it in "
        "the shared thread pool):\n" + "\n".join(violations)
    )


# The synchronous data core: repositories and the Version Engine. Async code
# lives at the edges and calls into it with run_blocking/run_owned.
_ASYNC_EDGES_IN_CORE = {
    # WebSocket fan-out and Redis pub/sub for commit notifications.
    "version_engine/derived/notifications.py": "async-only WebSocket/Redis clients",
    # ETL runtime state shares ARQ's async Redis connection.
    "platform/upload/state/repository.py": "async Redis client shared with ARQ",
    # Unreferenced modules kept until ISSUE-123 A4 deletes them.
    "version_engine/infrastructure/async_context.py": "unreferenced; pending deletion",
    "version_engine/write_engine/cas_backoff.py": "unreferenced; pending deletion",
}


def _is_data_core(relative: str) -> bool:
    name = relative.rsplit("/", 1)[-1]
    return (
        relative.startswith("version_engine/")
        or name == "repository.py"
        or name.endswith("_repository.py")
        or "/repositories/" in relative
    )


def test_repositories_and_version_engine_are_synchronous():
    violations = []
    for path, tree in _python_files():
        relative = path.relative_to(SOURCE).as_posix()
        if not _is_data_core(relative) or relative in _ASYNC_EDGES_IN_CORE:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                violations.append(f"{relative}:{node.lineno} async def {node.name}")
            elif isinstance(node, ast.Import) and any(a.name == "asyncio" for a in node.names):
                violations.append(f"{relative}:{node.lineno} import asyncio")
    assert violations == [], "\n".join(violations)


def test_async_edges_in_the_core_are_still_async():
    # Keep the allowlist honest: a module that became synchronous leaves it.
    for relative in _ASYNC_EDGES_IN_CORE:
        tree = ast.parse((SOURCE / relative).read_text(encoding="utf-8"))
        assert any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree)), relative
