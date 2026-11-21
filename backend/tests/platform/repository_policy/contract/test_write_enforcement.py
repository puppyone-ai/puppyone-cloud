"""Contract tests for repository write policy enforcement across all entry points."""

from __future__ import annotations

import base64
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.config import settings
from src.exception_handler import app_exception_handler
from src.exceptions import AppException
from src.file_operations.commands import VersionWriteCommandService
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.platform.access.adapters.api import repository_management
from src.platform.access.adapters.cli import context as cli_context
from src.platform.access.adapters.cli import router as cli_router
from src.platform.access.adapters.git.native_repository import NativeGitRepository
from src.platform.access.adapters.git.protocol import pkt_line
from src.platform.access.adapters.git.workspace_transport import RunPublication
from src.platform.access.adapters.mcp_endpoint import resolver as mcp_resolver
from src.platform.access.adapters.mcp_endpoint import runtime as mcp_runtime
from src.platform.access.filesystem.registry import FS_TOOL_BY_NAME
from src.platform.access.filesystem.service import ScopedFsService
from src.platform.project.write_lease import LeasedVersionWriteCommandService
from src.platform.repository_policy import (
    BLOCKED_ERROR_CODE,
    BLOCKED_MESSAGE,
    RepositoryWriteBlockedError,
    WriteChannel,
    reset_policy_cache,
)
from src.shared_types.grants import (
    ROLE_CAPABILITIES,
    GrantSource,
    ProjectGrant,
    ProjectRole,
    RuntimeGrant,
    RuntimeMode,
    RuntimePrincipal,
)
from src.shared_types.repository_target import ProjectRootTarget, ResolvedRepositoryView
from src.version_engine.domain.initialization import InitializationGrant
from src.version_engine.read.ref_metadata import GitBranchBase, RefState
from tests.repository_hosting.harness.git import Git
from tests.repository_hosting.harness.http_server import _serve_git_app

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _set_policy(monkeypatch, name: str) -> None:
    monkeypatch.setattr(settings, "REPOSITORY_POLICY", name)
    monkeypatch.setattr(settings, "REPOSITORY_POLICY_FILE", "")
    reset_policy_cache()


def _project_grant(project_id: str = "proj-1", *, writable: bool = True) -> ProjectGrant:
    role = ProjectRole.ADMIN if writable else ProjectRole.VIEWER
    return ProjectGrant(
        project_id=project_id,
        org_id="org-1",
        user_id="user-1",
        role=role,
        source=GrantSource.PROJECT_MEMBER,
        capabilities=ROLE_CAPABILITIES[role],
    )


def _runtime_grant(project_id: str = "proj-1", *, writable: bool = True) -> RuntimeGrant:
    target = ProjectRootTarget(project_id=project_id)
    mode = RuntimeMode.READ_WRITE if writable else RuntimeMode.READ
    return RuntimeGrant(
        principal=RuntimePrincipal(principal_id="ap-1", credential_kind="access_key"),
        target=target,
        repository_view=ResolvedRepositoryView(
            target=target,
            path_prefix="",
            excludes=(),
            max_mode=mode.value,
        ),
        mode=mode,
    )


def _forbid_lease(project_id: str, reason: str):
    raise AssertionError(f"ProjectWriteLease must not be acquired for blocked write ({reason})")


def _dummy_lease_factory(entered: list[str]):
    @asynccontextmanager
    async def factory(project_id: str, reason: str):
        entered.append(f"{project_id}:{reason}")
        yield

    return factory


def _assert_blocked_exception(exc: RepositoryWriteBlockedError, channel: WriteChannel) -> None:
    assert exc.status_code == 409
    assert exc.reason_code == BLOCKED_ERROR_CODE
    assert exc.message == BLOCKED_MESSAGE
    assert exc.details["code"] == BLOCKED_ERROR_CODE
    assert exc.details["message"] == BLOCKED_MESSAGE
    assert exc.details["channel"] == channel.value


def _assert_blocked_http_response(response, channel: WriteChannel) -> None:
    assert response.status_code == 409
    body = response.json()
    assert body["message"] == BLOCKED_MESSAGE
    assert body["data"]["code"] == BLOCKED_ERROR_CODE
    assert body["data"]["message"] == BLOCKED_MESSAGE
    assert body["data"]["channel"] == channel.value


def _stub_product_adapter(monkeypatch, *, grant):
    """Build a ProductOperationAdapter whose writer returns committed without DB/S3."""
    committed = {
        "status": "committed",
        "product": {
            "commit_oid": "a" * 40,
            "changes": [["add", base64.b64encode(b"doc.md").decode()]],
        },
    }

    class FakeWriter:
        def __init__(self, _service):
            pass

        def replay(self, _grant, **_kwargs):
            return None

        def apply(self, _grant, **_kwargs):
            return committed

    monkeypatch.setattr(
        "src.version_engine.write_engine.native_operation_writer.NativeOperationWriter",
        FakeWriter,
    )
    repos = SimpleNamespace(get_native_service=lambda _pid: object())
    return ProductOperationAdapter(repos, grant=grant)


# ---------------------------------------------------------------------------
# Checkpoint 1: ProductOperationAdapter.apply_native_command & product channels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_channel", "expected_channel"),
    [
        ("papi", WriteChannel.CONTENT_API),
        ("mcp", WriteChannel.MCP),
        ("access_cli", WriteChannel.CLI),
        ("upload", WriteChannel.UPLOAD),
        ("import", WriteChannel.IMPORT),
        ("sync", WriteChannel.SYNC),
        ("table", WriteChannel.TABLE),
        ("restore", WriteChannel.RESTORE),
    ],
)
async def test_product_write_channels_blocked_before_lease_in_vm_only_and_open_in_open(
    monkeypatch, source_channel, expected_channel
):
    grant = _project_grant()
    ops = _stub_product_adapter(monkeypatch, grant=grant)
    base = {"expected_oid": None, "tree_oid": EMPTY_TREE, "generation": 1}

    # vm-only: rejected with 409 before lease acquisition
    _set_policy(monkeypatch, "vm-only")
    with pytest.raises(RepositoryWriteBlockedError) as exc_info:
        await ops.apply_native_command(
            "proj-1",
            grant,
            channel=source_channel,
            request_key=str(uuid.uuid4()),
            base=base,
            input_sha256="0" * 64,
            splice=lambda _s, _t: (EMPTY_TREE, []),
            message="test",
            write_lease_factory=_forbid_lease,
        )
    _assert_blocked_exception(exc_info.value, expected_channel)

    # open: allowed and enters lease
    _set_policy(monkeypatch, "open")
    leases: list[str] = []
    result = await ops.apply_native_command(
        "proj-1",
        grant,
        channel=source_channel,
        request_key=str(uuid.uuid4()),
        base=base,
        input_sha256="0" * 64,
        splice=lambda _s, _t: (EMPTY_TREE, []),
        message="test",
        write_lease_factory=_dummy_lease_factory(leases),
    )
    assert result["status"] == "committed"
    assert leases == ["proj-1:product.native"]


async def test_leased_command_service_blocks_before_project_write_lease(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    grant = _project_grant()
    ops = _stub_product_adapter(monkeypatch, grant=grant)
    commands = LeasedVersionWriteCommandService(
        VersionWriteCommandService(ops),
        write_lease_factory=_forbid_lease,
    )

    for source_channel, expected_channel in (
        ("papi", WriteChannel.CONTENT_API),
        ("upload", WriteChannel.UPLOAD),
        ("import", WriteChannel.IMPORT),
        ("sync", WriteChannel.SYNC),
        ("table", WriteChannel.TABLE),
    ):
        with pytest.raises(RepositoryWriteBlockedError) as exc_info:
            await commands.write_bytes(
                "proj-1",
                "notes.md",
                b"hello",
                actor="user:1",
                source_channel=source_channel,
            )
        _assert_blocked_exception(exc_info.value, expected_channel)


async def test_restore_commit_blocked_in_vm_only(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    grant = _project_grant()
    repos = SimpleNamespace(
        get_native_service=Mock(side_effect=AssertionError("must not touch storage"))
    )
    ops = ProductOperationAdapter(repos, grant=grant)

    with pytest.raises(RepositoryWriteBlockedError) as exc_info:
        await ops.restore_commit("proj-1", "a" * 40)
    _assert_blocked_exception(exc_info.value, WriteChannel.RESTORE)


async def test_initialization_grant_and_template_seed_succeed_in_vm_only(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    fake_lease = SimpleNamespace(
        lease_id="lease-1",
        project_id="proj-1",
        initialization_actor="user-1",
        initialization_operation_key="init:proj-1",
        is_active=True,
    )
    init_grant = InitializationGrant("proj-1", "user-1", fake_lease)
    ops = _stub_product_adapter(monkeypatch, grant=init_grant)
    base = {"expected_oid": None, "tree_oid": EMPTY_TREE, "generation": 1}
    leases: list[str] = []

    # InitializationGrant always resolves to WriteChannel.INITIALIZATION even when
    # caller passes source_channel="papi" or "upload".
    result = await ops.apply_native_command(
        "proj-1",
        init_grant,
        channel="upload",
        request_key=str(uuid.uuid4()),
        base=base,
        input_sha256="0" * 64,
        splice=lambda _s, _t: (EMPTY_TREE, []),
        message="seed project",
        write_lease_factory=_dummy_lease_factory(leases),
    )
    assert result["status"] == "committed"
    assert leases == ["proj-1:product.native"]

    # Explicit source_channel="initialization" (used by seed_template_content / seed_default_content)
    human_grant = _project_grant()
    ops_human = _stub_product_adapter(monkeypatch, grant=human_grant)
    leases.clear()
    result2 = await ops_human.apply_native_command(
        "proj-1",
        human_grant,
        channel="initialization",
        request_key=str(uuid.uuid4()),
        base=base,
        input_sha256="0" * 64,
        splice=lambda _s, _t: (EMPTY_TREE, []),
        message="template seed",
        write_lease_factory=_dummy_lease_factory(leases),
    )
    assert result2["status"] == "committed"
    assert leases == ["proj-1:product.native"]


# ---------------------------------------------------------------------------
# Checkpoint 2: NativeGitRepository.receive (git_push, initial_attempt, workspace)
# ---------------------------------------------------------------------------


def _fake_git_service(git: Git, *, unborn: bool = False):
    head_oid = None if unborn else git.text("rev-parse", "HEAD")
    refs = [
        {
            "name_b64": base64.b64encode(b"HEAD").decode(),
            "state": {
                "kind": "symbolic",
                "target_b64": base64.b64encode(b"refs/heads/main").decode(),
            },
        }
    ]
    if head_oid:
        refs.append(
            {
                "name_b64": base64.b64encode(b"refs/heads/main").decode(),
                "state": {"kind": "oid", "oid": head_oid},
                "kind": "commit",
            }
        )
    snapshot_wire = {
        "project_id": "proj-1",
        "authority": "native",
        "generation": 1,
        "ref_sequence": 1,
        "object_format": "sha1",
        "write_state": "active",
        "refs": refs,
    }
    submitted: list[dict] = []

    class FakeControl:
        def begin_read(self, project_id, actor, pin):
            return {**snapshot_wire, "pin_id": pin}

        def end_read(self, project_id, actor, pin):
            return None

        def read_snapshot(self, project_id, actor):
            return snapshot_wire

        def check_read_pin(self, project_id, actor, pin):
            return True

    class FakeBackend:
        publication_project_id = "proj-1"

        def pinned_reader(self, _snapshot):
            return self

        def put_git_pack_durable(self, _incoming):
            return None

    class FakeService:
        project_id = "proj-1"
        object_format = "sha1"
        control = FakeControl()
        backend = FakeBackend()
        proof_factory = None

        def submit(self, _grant, **kwargs):
            submitted.append(kwargs)
            return {"status": "committed"}

    return FakeService(), submitted


def test_git_push_blocked_in_vm_only_with_clear_utf8_refusal_and_allowed_in_open(
    tmp_path, monkeypatch
):
    git = Git.init(tmp_path / "repo")
    oid = git.commit({"hello.txt": b"v1"})
    pack = git.run("pack-objects", "--stdout", "--revs", input=(oid + "\n").encode()).stdout
    req = tmp_path / "receive-pack"
    req.write_bytes(
        pkt_line(f"{'0' * 40} {oid} refs/heads/main\0report-status side-band-64k\n".encode())
        + b"0000"
        + pack
    )
    service, submitted = _fake_git_service(git, unborn=True)

    # vm-only: ordinary git push is refused before snapshot/pack/submit with UTF-8 message
    _set_policy(monkeypatch, "vm-only")
    service.control.begin_read = Mock(side_effect=AssertionError("must not open snapshot"))
    repo = NativeGitRepository(service)
    response = repo.receive(_project_grant(), req, channel=WriteChannel.GIT_PUSH)
    assert response.status_code == 200
    assert f"ng refs/heads/main {BLOCKED_MESSAGE}".encode() in response.body
    assert submitted == []

    # vm-only: initial_attempt creating unborn main IS allowed (initialization channel)
    service2, submitted2 = _fake_git_service(git, unborn=True)
    response_init = NativeGitRepository(service2).receive(
        _project_grant(),
        req,
        channel=WriteChannel.GIT_PUSH,
        initial_attempt=str(uuid.uuid4()),
    )
    assert b"ok refs/heads/main" in response_init.body
    assert len(submitted2) == 1

    # vm-only: workspace publication IS allowed (workspace channel)
    service3, submitted3 = _fake_git_service(git, unborn=True)
    base = GitBranchBase("sha1", 1, b"refs/heads/main", RefState(), RefState(target=b"refs/heads/main"))
    pub = RunPublication(str(uuid.uuid4()), base, oid)
    response_ws = NativeGitRepository(service3).receive(
        _project_grant(),
        req,
        channel=WriteChannel.WORKSPACE,
        publication=pub,
    )
    assert b"ok refs/heads/main" in response_ws.body
    assert len(submitted3) == 1

    # vm-only: claiming workspace channel without a publication is refused
    with pytest.raises(PermissionError, match="admitted publication"):
        NativeGitRepository(service3).receive(
            _project_grant(),
            req,
            channel=WriteChannel.WORKSPACE,
            publication=None,
        )

    # open: ordinary git push succeeds
    _set_policy(monkeypatch, "open")
    service4, submitted4 = _fake_git_service(git, unborn=True)
    response_open = NativeGitRepository(service4).receive(
        _project_grant(), req, channel=WriteChannel.GIT_PUSH
    )
    assert b"ok refs/heads/main" in response_open.body
    assert len(submitted4) == 1


def test_stock_git_cli_push_sees_utf8_refusal_message_in_vm_only(tmp_path, monkeypatch):
    """A real `git push` client prints the exact Chinese workspace-only refusal."""
    import asyncio

    _set_policy(monkeypatch, "vm-only")
    git = Git.init(tmp_path / "client")
    git.commit({"readme.md": b"hello"})
    service, submitted = _fake_git_service(git, unborn=True)
    transport = NativeGitRepository(service)
    app = FastAPI()

    @app.get("/repo.git/info/refs")
    async def info(service: str, request: Request):
        return await asyncio.to_thread(
            transport.info_refs,
            _project_grant(),
            service,
            protocol=request.headers.get("git-protocol", ""),
        )

    @app.post("/repo.git/git-receive-pack")
    async def receive(request: Request):
        spool = tmp_path / f"rpc-{uuid.uuid4().hex}"
        spool.write_bytes(await request.body())
        try:
            return await asyncio.to_thread(
                transport.receive, _project_grant(), spool, channel=WriteChannel.GIT_PUSH
            )
        finally:
            spool.unlink()

    with _serve_git_app(app) as address:
        git.run("remote", "add", "origin", address + "/repo.git")
        result = git.run("push", "origin", "main", check=False)

    assert result.returncode != 0
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert BLOCKED_MESSAGE in stderr, stderr
    assert submitted == []


# ---------------------------------------------------------------------------
# Checkpoint 3: repository_management.py (HEAD updates)
# ---------------------------------------------------------------------------


def test_repository_head_update_blocked_before_lease_in_vm_only_and_allowed_in_open(monkeypatch):
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(repository_management.management_router, prefix="/api/v1/content")

    grant = _project_grant()
    auth_dep = SimpleNamespace(grant=grant)
    fake_service = SimpleNamespace(
        object_format="sha1",
        submit=lambda *_args, **_kwargs: {"status": "committed", "ref_sequence": 2},
    )
    fake_manager = SimpleNamespace(get_native_service=lambda _pid: fake_service)

    app.dependency_overrides[repository_management.get_repo_manager] = lambda: fake_manager
    for route in app.routes:
        if getattr(route, "path", "").endswith("/{project_id}/head"):
            for dep in route.dependant.dependencies:
                if dep.name == "authorized":
                    app.dependency_overrides[dep.call] = lambda: auth_dep

    payload = {
        "request_key": str(uuid.uuid4()),
        "generation": 1,
        "expected": {
            "kind": "symbolic",
            "target_b64": base64.b64encode(b"refs/heads/main").decode(),
        },
        "target_branch": "release",
    }

    # vm-only: HTTP 409 before ProjectWriteLease
    _set_policy(monkeypatch, "vm-only")
    monkeypatch.setattr(repository_management, "ProjectWriteLease", _forbid_lease)
    client = TestClient(app)
    resp = client.put("/api/v1/content/proj-1/head", json=payload)
    _assert_blocked_http_response(resp, WriteChannel.HEAD)

    # open: succeeds through ProjectWriteLease
    _set_policy(monkeypatch, "open")
    leases: list[str] = []
    monkeypatch.setattr(repository_management, "ProjectWriteLease", _dummy_lease_factory(leases))
    resp_open = client.put("/api/v1/content/proj-1/head", json=payload)
    assert resp_open.status_code == 200
    assert leases == ["proj-1:repository.head"]


# ---------------------------------------------------------------------------
# MCP read-only downgrade + direct write call refusal
# ---------------------------------------------------------------------------


def test_mcp_tools_downgraded_to_read_only_in_vm_only_and_restored_in_open(monkeypatch):
    surface = {"id": "surf-1", "kind": "mcp", "project_id": "proj-1", "created_by": "user-1", "name": "Test MCP"}
    policy = {"fs_policy": {"accesses": [{"path": "", "readonly": False}]}}
    credential = {"id": "cred-1"}

    monkeypatch.setattr(mcp_resolver, "_resolve_surface", lambda _key: (surface, policy, credential))
    monkeypatch.setattr(mcp_resolver, "_owner_grant", lambda _pid, _uid: _project_grant(_pid, writable=True))

    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(mcp_runtime.router)
    app.dependency_overrides[mcp_runtime.verify_internal_secret] = lambda: None
    app.dependency_overrides[mcp_runtime.get_product_operation_adapter] = lambda: SimpleNamespace(
        for_grant=lambda _g: SimpleNamespace()
    )
    app.dependency_overrides[mcp_runtime.get_version_write_command_service] = lambda: SimpleNamespace(
        ops=SimpleNamespace(for_grant=lambda _g: SimpleNamespace())
    )
    client = TestClient(app)

    write_tool_names = {
        name for name, spec in FS_TOOL_BY_NAME.items() if spec.access in {"write", "delete"}
    }

    # vm-only: mode forced to ro, zero write/delete tools advertised, direct call returns 409
    _set_policy(monkeypatch, "vm-only")
    tools_resp = client.post("/internal/mcp-runtime/tools", json={"api_key": "mcp_test_key"})
    assert tools_resp.status_code == 200
    tools_data = tools_resp.json()
    assert tools_data["endpoint"]["mode"] == "ro"
    advertised = {tool["name"] for tool in tools_data["tools"]}
    assert advertised.isdisjoint(write_tool_names)
    assert "fs_ls" in advertised and "fs_cat" in advertised

    call_resp = client.post(
        "/internal/mcp-runtime/call",
        json={"api_key": "mcp_test_key", "name": "fs_write", "arguments": {"path": "a.md", "content": "hi"}},
    )
    _assert_blocked_http_response(call_resp, WriteChannel.MCP)

    # open: mode is rw and write tools reappear
    _set_policy(monkeypatch, "open")
    tools_open = client.post("/internal/mcp-runtime/tools", json={"api_key": "mcp_test_key"})
    assert tools_open.status_code == 200
    open_data = tools_open.json()
    assert open_data["endpoint"]["mode"] == "rw"
    open_advertised = {tool["name"] for tool in open_data["tools"]}
    assert "fs_write" in open_advertised
    assert "fs_mkdir" in open_advertised


# ---------------------------------------------------------------------------
# CLI read-only downgrade + write command refusal
# ---------------------------------------------------------------------------


def test_cli_semantics_read_only_and_writes_blocked_in_vm_only_and_restored_in_open(monkeypatch):
    grant = _runtime_grant("proj-1", writable=True)
    auth = {"agent": "ap-1", "name": "CLI AP", "_runtime_grant": grant}
    facade = SimpleNamespace(
        scope_path="",
        mode="rw",
        repo_id="proj-1",
        kind="project",
        ref="refs/heads/main",
        object_store_scope="",
        excludes=[],
    )
    view = grant.repository_view

    monkeypatch.setattr(cli_context, "resolve_access_point", lambda _k, channel="cli": ("proj-1", auth))
    monkeypatch.setattr(cli_context, "repo_facade_from_auth", lambda *_a, **_kw: facade)
    monkeypatch.setattr(cli_context, "repository_view_from_auth", lambda _a: view)
    monkeypatch.setattr(
        "src.platform.access.connector_policy.get_connector_policy_snapshot",
        lambda *_a, **_kw: SimpleNamespace(
            connector_id="conn-1",
            provider="cli",
            status="active",
            allowed_commands=frozenset({"semantics", "ls", "write", "mkdir", "rm"}),
        ),
    )

    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(cli_router.router)

    fake_ops = SimpleNamespace(
        for_grant=lambda _g: SimpleNamespace(
            write_file=AsyncMock(return_value=SimpleNamespace(commit_id="b" * 40, paths=["a.md"]))
        )
    )
    app.dependency_overrides[
        __import__(
            "src.file_operations.dependencies", fromlist=["get_version_write_command_service"]
        ).get_version_write_command_service
    ] = lambda: VersionWriteCommandService(fake_ops)

    client = TestClient(app)
    headers = {"X-Access-Key": "ak_test", "X-Puppy-Client": "cli"}

    write_tool_names = {
        name for name, spec in FS_TOOL_BY_NAME.items() if spec.access in {"write", "delete"}
    }

    # vm-only: semantics reports read-only; write command returns 409
    _set_policy(monkeypatch, "vm-only")
    sem = client.get("/ap-fs/semantics", headers=headers)
    assert sem.status_code == 200
    sem_data = sem.json()["data"]
    assert sem_data["scope"]["mode"] == "r"
    assert {tool["name"] for tool in sem_data["tools"]}.isdisjoint(write_tool_names)

    write_resp = client.post(
        "/ap-fs/write",
        headers=headers,
        json={"path": "hello.md", "content": "hi", "node_type": "markdown"},
    )
    _assert_blocked_http_response(write_resp, WriteChannel.CLI)

    # open: semantics reports writable and write command reaches command service
    _set_policy(monkeypatch, "open")
    sem_open = client.get("/ap-fs/semantics", headers=headers)
    assert sem_open.status_code == 200
    sem_open_data = sem_open.json()["data"]
    assert sem_open_data["scope"]["mode"] == "rw"
    assert "fs_write" in {tool["name"] for tool in sem_open_data["tools"]}

    write_open = client.post(
        "/ap-fs/write",
        headers=headers,
        json={"path": "hello.md", "content": "hi", "node_type": "markdown"},
    )
    assert write_open.status_code == 200


async def test_scoped_fs_service_cli_channel_uses_cli_policy(monkeypatch):
    _set_policy(monkeypatch, "vm-only")
    from src.platform.access.filesystem.context import ScopedFsContext

    ctx = ScopedFsContext(
        api_key="ak_test",
        endpoint_id="ep-1",
        endpoint_name="CLI",
        project_id="proj-1",
        user_id="user-1",
        scope_id="",
        scope_path="",
        mode="rw",
        exclude=[],
        channel="cli",
    )
    svc = ScopedFsService(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(RepositoryWriteBlockedError) as exc_info:
        await svc.call(ctx, "fs_write", {"path": "a.md", "content": "hi"})
    _assert_blocked_exception(exc_info.value, WriteChannel.CLI)
