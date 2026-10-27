"""Keep data access constraints enforceable at every future call site."""

import ast
from pathlib import Path

BASE = Path(__file__).resolve().parents[3] / "src/platform/access/adapters/agent"


def test_application_and_heartbeat_cannot_bypass_named_ports():
    files = [BASE / "service.py", BASE / "router.py", *BASE.joinpath("runtime").rglob("*.py")]
    for path in files:
        if "persistence" in path.parts or path.name == "worker.py":
            continue  # Storage adapters and composition, not business orchestration.
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"table", "rpc", "from_"}, (path.name, node.lineno)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    (
                        "src.infra.supabase.client",
                        "src.infra.supabase.dependencies",
                        "postgrest",
                        "supabase",
                    )
                ), path
                if path.name == "heartbeat.py":
                    assert not any(
                        name in (node.module or "")
                        for name in ("admission", "config", "persistence")
                    )
    heartbeat = (BASE / "runtime/control/heartbeat.py").read_text()
    assert "RenewalPort" in heartbeat
    assert "recheck" not in heartbeat


def test_context_contains_no_implicit_storage_access():
    tree = ast.parse((BASE / "runtime/control/context.py").read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert all(
        not any(part in (module or "") for part in ("repository", "supabase", "dependencies"))
        for module in imports
    )
    assert "frozen=True" in (BASE / "runtime/control/context.py").read_text()


def test_query_adapter_contains_no_table_mutations():
    tree = ast.parse((BASE / "runtime/persistence/queries.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"insert", "update", "upsert", "delete"}


def test_model_and_tool_boundaries_use_atomic_commands():
    tree = ast.parse((BASE / "runtime/control/runner.py").read_text())
    for method in ast.walk(tree):
        if not isinstance(method, ast.AsyncFunctionDef) or method.name not in {
            "_model",
            "tool_start",
            "tool_end",
            "bound_tool",
        }:
            continue
        for node in ast.walk(method):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"check", "write", "tool", "tools"}, (
                    method.name,
                    node.lineno,
                )


def test_control_depends_on_capability_contracts_only():
    prefix = "src.platform.access.adapters.agent.runtime."
    for path in BASE.joinpath("runtime/control").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                for capability in ("loop", "execution", "filesystem"):
                    if (node.module or "").startswith(prefix + capability + "."):
                        assert node.module == prefix + capability + ".port", (path, node.lineno)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(tuple(prefix + name + "." for name in (
                        "loop", "execution", "filesystem"
                    ))), (path, node.lineno)


def test_loop_specific_state_and_provider_sdks_do_not_leak_into_control():
    for filename in ("runner.py", "workspace.py"):
        for node in ast.walk(ast.parse(BASE.joinpath("runtime/control", filename).read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in {"pi_version", "pi_result", "leaf_id", "entries", "run_entry_start"}
    for path in BASE.joinpath("runtime").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                       else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            assert not any(name.split(".")[0] in {"e2b", "docker", "fly"} for name in modules), path
