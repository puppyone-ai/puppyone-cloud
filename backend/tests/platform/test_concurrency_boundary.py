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
