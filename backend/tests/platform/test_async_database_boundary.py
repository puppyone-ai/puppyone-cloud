"""Coroutines never run the synchronous Supabase client on the event loop.

Synchronous data access stays in synchronous repository/service functions.
Async callers enter it through ``asyncio.to_thread``; route handlers that only
do blocking work are plain ``def`` handlers that FastAPI runs in its threadpool.
"""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src"


def _direct_execute_calls(function: ast.AsyncFunctionDef):
    nested = set()
    for node in ast.walk(function):
        if node is not function and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            nested.update(id(inner) for inner in ast.walk(node))
    for node in ast.walk(function):
        if (
            id(node) not in nested
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and not node.args
        ):
            chain = ast.unparse(node.func.value)
            if ".table(" in chain or ".rpc(" in chain:
                yield node.lineno


def test_async_functions_do_not_execute_supabase_queries_inline():
    violations = []
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                violations.extend(
                    f"{path.relative_to(SOURCE.parent)}:{line} {node.name}"
                    for line in _direct_execute_calls(node)
                )
    assert violations == []
