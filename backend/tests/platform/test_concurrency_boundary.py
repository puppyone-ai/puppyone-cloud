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
# Removed by the Version Engine storage slice of this change.
_PENDING_REMOVAL = {
    "version_engine/storage/backends/s3.py",
    "version_engine/derived/object_integrity_worker.py",
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
        if relative in _PENDING_REMOVAL:
            continue
        if relative in _ENTRY_POINTS:
            entry_point_violations.extend(found)
        else:
            violations.extend(found)
    assert violations == [], "\n".join(violations)
    assert entry_point_violations == [], "entry points create loops only in main():\n" + "\n".join(
        entry_point_violations
    )


def test_pending_loop_creation_list_only_shrinks():
    still_creating = {
        relative
        for relative in _PENDING_REMOVAL
        if list(_loop_creation(ast.parse((SOURCE / relative).read_text(encoding="utf-8"))))
    }
    assert still_creating == _PENDING_REMOVAL, "remove fixed modules from _PENDING_REMOVAL"
