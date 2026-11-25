"""Static & signature architecture guards for repository write policy enforcement.

Every repository write converges on three entry points:
1. ProductOperationAdapter.apply_native_command (product writes)
2. NativeGitRepository.receive (Git smart-HTTP & workspace transport)
3. repository_management (default branch / HEAD update)

New call sites cannot omit the channel argument or bypass the chokepoints.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from src.file_operations.operation_adapter import ProductOperationAdapter
from src.platform.access.adapters.git.native_repository import NativeGitRepository

BACKEND = Path(__file__).resolve().parents[2]
SRC = BACKEND / "src"

_RUNNER_FUNCTIONS = frozenset({"run_owned", "run_blocking"})
_DEPLOYED_POLICY_LOADERS = {
    "src.main": None,
    "src.platform.agent.runtime.worker": "main",
    "src.platform.access.adapters.ssh.gateway": "main",
    "src.platform.chat_apps.gateway": "main",
    "src.platform.hosts.gateway_service": None,
    "src.platform.imports.worker": "startup",
    "src.platform.synchronize.worker": "startup",
    "src.platform.upload.worker": "startup",
}


def _call_has_channel_kwarg(call: ast.Call) -> bool:
    return any(kw.arg == "channel" for kw in call.keywords)


def _attr_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_git_receive_file(path: Path) -> bool:
    rel = path.relative_to(SRC).as_posix()
    return rel.startswith("platform/access/adapters/git/") or "NativeGitRepository" in path.read_text(
        encoding="utf-8"
    )


def test_chokepoint_signatures_require_keyword_only_channel():
    for method in (ProductOperationAdapter.apply_native_command, NativeGitRepository.receive):
        sig = inspect.signature(method)
        assert "channel" in sig.parameters, f"{method.__qualname__} must declare channel"
        param = sig.parameters["channel"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{method.__qualname__} channel must be keyword-only"
        )
        assert param.default is inspect.Parameter.empty, (
            f"{method.__qualname__} channel must have no default"
        )


def test_every_apply_native_command_call_passes_channel():
    missing: list[str] = []
    seen = 0
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SRC).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _attr_name(node.func)
            if name == "apply_native_command" or (
                name in _RUNNER_FUNCTIONS
                and node.args
                and _attr_name(node.args[0]) == "apply_native_command"
            ):
                seen += 1
                if not _call_has_channel_kwarg(node):
                    missing.append(f"{rel}:{node.lineno}")
    assert seen >= 3, f"Expected at least 3 apply_native_command call sites, found {seen}"
    assert missing == [], f"apply_native_command calls missing channel=...: {missing}"


def test_every_git_receive_call_passes_channel():
    missing: list[str] = []
    seen = 0
    for path in sorted(SRC.rglob("*.py")):
        if not _is_git_receive_file(path):
            continue
        rel = path.relative_to(SRC).as_posix()
        # NativeGitEndpoint.receive is an HTTP adapter wrapper that immediately
        # delegates to NativeGitRepository.receive(..., channel=WriteChannel.GIT_PUSH).
        if rel == "platform/access/adapters/git/router.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _attr_name(node.func)
            if (name == "receive" and isinstance(node.func, ast.Attribute)) or (
                name in _RUNNER_FUNCTIONS
                and node.args
                and isinstance(node.args[0], ast.Attribute)
                and node.args[0].attr == "receive"
            ):
                seen += 1
                if not _call_has_channel_kwarg(node):
                    missing.append(f"{rel}:{node.lineno}")
    assert seen >= 2, f"Expected at least 2 NativeGitRepository.receive call sites, found {seen}"
    assert missing == [], f"NativeGitRepository.receive calls missing channel=...: {missing}"


def test_native_operation_writer_is_only_constructed_in_operation_adapter():
    callers: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in {
            "file_operations/operation_adapter.py",
            "version_engine/write_engine/native_operation_writer.py",
        }:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _attr_name(node.func) == "NativeOperationWriter":
                callers.append(f"{rel}:{node.lineno}")
    assert callers == [], f"NativeOperationWriter bypassed ProductOperationAdapter: {callers}"


def test_repository_head_management_checks_write_policy():
    path = SRC / "platform/access/adapters/api/repository_management.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    funcs = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for name in ("change_head", "update_repository_head"):
        node = funcs[name]
        has_check = any(
            isinstance(child, ast.Call) and _attr_name(child.func) == "require_write"
            for child in ast.walk(node)
        )
        assert has_check, f"{name} must enforce require_write(WriteChannel.HEAD)"


def test_every_deployed_process_loads_repository_policy_at_startup():
    script = (BACKEND / "scripts" / "start-service.sh").read_text(encoding="utf-8")
    commands = re.findall(r"exec (?:uvicorn|arq|python -m) ([\w.]+)", script)
    deployed = {name.removesuffix(".WorkerSettings") for name in commands if name.startswith("src.")}
    assert deployed == set(_DEPLOYED_POLICY_LOADERS)

    for module, function in _DEPLOYED_POLICY_LOADERS.items():
        tree = ast.parse((BACKEND / f"{module.replace('.', '/')}.py").read_text(encoding="utf-8"))
        if function is None:
            stmts = [n for n in tree.body if not isinstance(n, ast.ClassDef | ast.FunctionDef)]
        else:
            stmts = next(
                n.body
                for n in tree.body
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == function
            )
        loads = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "load_repository_policy"
            for stmt in stmts
            for node in ast.walk(stmt)
        )
        assert loads, f"{module} ({function or 'module top-level'}) must call load_repository_policy()"
