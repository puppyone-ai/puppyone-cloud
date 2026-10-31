"""Storage writes a Project's data only under admission its process installed."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.infra import write_admission
from src.infra.s3.service import S3Service
from src.platform.project import write_lease
from src.version_engine.infrastructure.supabase.repo_manager import VersionRepoManager

BACKEND = Path(__file__).resolve().parents[2]
OWNED_KEY = "projects/project-1/file.txt"

# Every deployed process that can reach storage, and where it installs admission.
INSTALLED_IN = {
    "src.main": None,
    "src.platform.access.adapters.agent.runtime.worker": "main",
    "src.platform.access.adapters.ssh.gateway": "main",
    "src.platform.imports.worker": "startup",
    "src.platform.synchronize.worker": "startup",
    "src.platform.upload.worker": "startup",
}


def _deployed_processes() -> set[str]:
    script = (BACKEND / "scripts" / "start-service.sh").read_text(encoding="utf-8")
    commands = re.findall(r"exec (?:uvicorn|arq|python -m) ([\w.]+)", script)
    return {name.removesuffix(".WorkerSettings") for name in commands if name.startswith("src.")}


def _body(module: str, function: str | None) -> list[ast.stmt]:
    tree = ast.parse((BACKEND / f"{module.replace('.', '/')}.py").read_text(encoding="utf-8"))
    if function is None:
        return [node for node in tree.body if not isinstance(node, ast.ClassDef | ast.FunctionDef)]
    return next(
        node.body
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function
    )


def _installs(statements: list[ast.stmt]) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "install_storage_write_admission"
        for statement in statements
        for node in ast.walk(statement)
    )


def test_every_deployed_process_is_accounted_for():
    assert _deployed_processes() == set(INSTALLED_IN)


@pytest.mark.parametrize(("module", "function"), INSTALLED_IN.items())
def test_each_process_installs_admission_at_startup(module, function):
    assert _installs(_body(module, function))


def test_the_api_process_installs_admission_when_it_loads():
    probe = (
        "import src.main\n"
        "from src.infra.write_admission import project_write_admission\n"
        "print(type(project_write_admission()).__name__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "ProjectLeaseAdmission"


@pytest.fixture
def no_admission(monkeypatch):
    monkeypatch.setattr(write_admission, "_installed", None)


@pytest.fixture
def storage():
    service = object.__new__(S3Service)
    service.client = Mock()
    service.bucket_name = "bucket"
    service.max_file_size = 1024
    service.multipart_threshold = 1024
    return service


WRITES = {
    "upload_file": lambda s3: s3.upload_file(OWNED_KEY, b"data"),
    "create_multipart_upload": lambda s3: s3.create_multipart_upload(OWNED_KEY),
    "upload_part": lambda s3: s3.upload_part(OWNED_KEY, "upload", 1, b"data"),
    "complete_multipart_upload": lambda s3: s3.complete_multipart_upload(
        OWNED_KEY, "upload", [(1, "etag")]
    ),
    "copy_object": lambda s3: s3.copy_object("projects/project-1/source.txt", OWNED_KEY),
}


@pytest.mark.usefixtures("no_admission")
@pytest.mark.parametrize("write", WRITES.values(), ids=WRITES.keys())
def test_storage_refuses_project_writes_without_admission(storage, write):
    with pytest.raises(RuntimeError, match="not installed"):
        write(storage)
    assert storage.client.method_calls == []


@pytest.mark.usefixtures("no_admission")
def test_storage_writes_keys_no_project_owns_without_admission(storage):
    entered = []
    with storage._project_write_guard("public/logo.png", "put_object"):
        entered.append(True)
    assert entered == [True]


def _ref_authority():
    manager = VersionRepoManager(Mock(), SimpleNamespace(client=Mock()))
    metadata = {"project_id": "project-1", "object_format": "sha1"}
    return manager.service_from_metadata("project-1", metadata).control


@pytest.mark.usefixtures("no_admission")
def test_a_ref_write_is_refused_without_admission():
    with pytest.raises(RuntimeError, match="not installed"):
        _ref_authority().lease_provider("project-1")


def test_a_ref_write_uses_the_callers_live_lease(monkeypatch):
    lease = object()
    monkeypatch.setattr(write_lease, "active_project_write_lease", lambda _project: lease)
    write_lease.install_storage_write_admission()
    assert _ref_authority().lease_provider("project-1") is lease
