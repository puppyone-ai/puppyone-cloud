"""沙盒服务测试"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.platform.scope_sandbox.execution import docker_sandbox as docker_sandbox_module
from src.platform.scope_sandbox.execution.docker_sandbox import DockerSandbox
from src.platform.scope_sandbox.execution.e2b_sandbox import E2BSandbox
from src.platform.scope_sandbox.execution.service import SandboxService, get_sandbox_type
from src.platform.scope_sandbox.execution.store import InMemoryExecutionSessionStore
from src.platform.scope_sandbox.execution_policy import (
    SandboxCommandRejected,
    assert_command_allowed,
)

# ==================== Fake E2B 实现 ====================

class FakeFiles:
    """模拟 E2B files 接口"""
    def __init__(self):
        self._store = {}

    async def write(self, path: str, content: str):
        self._store[path] = content

    async def read(self, path: str):
        return self._store[path]


class FakeCommands:
    """模拟 E2B commands 接口"""
    def run(self, command: str, **_kwargs):
        return type("Result", (), {"text": f"ran: {command}"})


class FakeSandbox:
    """模拟 E2B Sandbox"""
    def __init__(self):
        self.files = FakeFiles()
        self.commands = FakeCommands()
        self.id = "fake-e2b-sandbox"
        self.closed = False

    async def close(self):
        self.closed = True


# ==================== Fixtures ====================

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def e2b_sandbox_service():
    """创建使用 Fake E2B 的 SandboxService"""
    sandbox = FakeSandbox()
    e2b_impl = E2BSandbox(
        sandbox_factory=lambda: sandbox,
        sandbox_connector=lambda _sandbox_id: sandbox,
        session_store=InMemoryExecutionSessionStore(),
    )
    return SandboxService(sandbox_impl=e2b_impl)


@pytest.fixture
def sandbox_service():
    """向后兼容：使用 sandbox_factory 参数"""
    sandbox = FakeSandbox()
    return SandboxService(sandbox_impl=E2BSandbox(
        sandbox_factory=lambda: sandbox,
        sandbox_connector=lambda _sandbox_id: sandbox,
        session_store=InMemoryExecutionSessionStore(),
    ))


def _docker() -> DockerSandbox:
    return DockerSandbox(session_store=InMemoryExecutionSessionStore())


# ==================== E2B Sandbox 测试 ====================

@pytest.mark.anyio
async def test_e2b_sandbox_start_requires_data(e2b_sandbox_service):
    """测试 start() 需要数据参数"""
    result = await e2b_sandbox_service.start(
        session_id="s1", data=None, readonly=False
    )
    assert result["success"] is False
    assert "data is required" in result["error"]


@pytest.mark.anyio
async def test_e2b_sandbox_exec_read_and_stop(e2b_sandbox_service):
    """测试 E2B 沙盒的基本流程"""
    # 启动
    await e2b_sandbox_service.start(session_id="s1", data={"a": 1}, readonly=False)
    
    # 执行命令
    exec_result = await e2b_sandbox_service.exec("s1", "echo ok")
    assert exec_result["success"] is True
    assert "echo ok" in exec_result["output"]
    
    # 读取数据
    read_result = await e2b_sandbox_service.read("s1")
    assert read_result["success"] is True
    assert read_result["data"] == {"a": 1}
    
    # 停止
    stop_result = await e2b_sandbox_service.stop("s1")
    assert stop_result["success"] is True


@pytest.mark.anyio
async def test_e2b_sandbox_status(e2b_sandbox_service):
    """测试沙盒状态查询"""
    # 未启动时
    status = await e2b_sandbox_service.status("s1")
    assert status["active"] is False

    # 启动后
    await e2b_sandbox_service.start(session_id="s1", data={"test": 1}, readonly=True)
    status = await e2b_sandbox_service.status("s1")
    assert status["active"] is True
    assert status["readonly"] is True

    # 停止后
    await e2b_sandbox_service.stop("s1")
    status = await e2b_sandbox_service.status("s1")
    assert status["active"] is False


@pytest.mark.anyio
async def test_project_execution_creation_is_leased_and_persists_ownership():
    sandbox = FakeSandbox()
    store = InMemoryExecutionSessionStore()
    impl = E2BSandbox(
        sandbox_factory=lambda: sandbox,
        sandbox_connector=lambda _sandbox_id: sandbox,
        session_store=store,
    )
    leases: list[tuple[str, str]] = []

    class Lease:
        def __init__(self, project_id, operation, **_kwargs):
            leases.append((project_id, operation))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    service = SandboxService(sandbox_impl=impl, write_lease_factory=Lease)

    result = await service.start(
        "owned-session",
        {"value": 1},
        False,
        audit_context={"project_id": "project-1", "source": "test"},
    )

    assert result["success"] is True
    assert store.get("owned-session").project_id == "project-1"
    assert leases == [("project-1", "sandbox.execution.start")]


@pytest.mark.anyio
async def test_e2b_sandbox_read_file(e2b_sandbox_service):
    """测试读取指定文件"""
    await e2b_sandbox_service.start(session_id="s1", data={"key": "value"}, readonly=False)
    
    result = await e2b_sandbox_service.read_file("s1", "/workspace/data.json", parse_json=True)
    assert result["success"] is True
    assert result["content"] == {"key": "value"}


# ==================== 向后兼容测试 ====================

@pytest.mark.anyio
async def test_sandbox_start_requires_data(sandbox_service):
    """向后兼容：原有测试保持通过"""
    result = await sandbox_service.start(
        session_id="s1", data=None, readonly=False
    )
    assert result["success"] is False


@pytest.mark.anyio
async def test_sandbox_exec_read_and_stop(sandbox_service):
    """向后兼容：原有测试保持通过"""
    await sandbox_service.start(session_id="s1", data={"a": 1}, readonly=False)
    exec_result = await sandbox_service.exec("s1", "echo ok")
    assert exec_result["success"] is True
    assert "echo ok" in exec_result["output"]

    read_result = await sandbox_service.read("s1")
    assert read_result["success"] is True
    assert read_result["data"] == {"a": 1}

    stop_result = await sandbox_service.stop("s1")
    assert stop_result["success"] is True


@pytest.mark.parametrize(
    "command",
    [
        "cat /etc",
        "cat /etc/passwd",
        "ls /proc",
        "curl 169.254.169.254/latest/meta-data",
        "curl 2852039166/latest/meta-data",
        "curl 0xA9FEA9FE/latest/meta-data",
    ],
)
def test_shared_command_policy_rejects_common_sensitive_representations(command):
    with pytest.raises(SandboxCommandRejected):
        assert_command_allowed(command)


@pytest.mark.anyio
async def test_shared_exec_audits_success_and_policy_rejection(sandbox_service):
    await sandbox_service.start(session_id="audit", data={"a": 1}, readonly=False)
    with patch(
        "src.platform.analytics.service.log_bash_execution",
        new_callable=AsyncMock,
    ) as audit:
        result = await sandbox_service.exec(
            "audit",
            "API_TOKEN=secret echo ok",
            audit_context={"source": "sandbox_endpoint"},
        )
        assert result["success"] is True
        assert audit.await_args.kwargs["source"] == "sandbox_endpoint"
        assert audit.await_args.kwargs["decision"] == "allowed"
        assert "secret" not in audit.await_args.kwargs["command"]

        with pytest.raises(SandboxCommandRejected):
            await sandbox_service.exec(
                "audit",
                "cat /proc",
                audit_context={"source": "chat_agent"},
            )
        assert audit.await_args.kwargs["source"] == "chat_agent"
        assert audit.await_args.kwargs["decision"] == "rejected"
        assert audit.await_args.kwargs["success"] is False


# ==================== 工厂模式测试 ====================

def test_sandbox_type_property():
    """测试 sandbox_type 属性"""
    # E2B 实现
    e2b_impl = E2BSandbox(
        sandbox_factory=lambda: FakeSandbox(),
        session_store=InMemoryExecutionSessionStore(),
    )
    service = SandboxService(sandbox_impl=e2b_impl)
    assert service.sandbox_type == "e2b"
    
    # Docker 实现（不启动实际 Docker）
    docker_impl = _docker()
    service = SandboxService(sandbox_impl=docker_impl)
    assert service.sandbox_type == "docker"


def test_get_sandbox_type_with_e2b_key():
    """测试有 E2B_API_KEY 时返回 e2b"""
    with patch.dict(os.environ, {"E2B_API_KEY": "test-key"}):
        with patch("src.config.settings") as mock_settings:
            mock_settings.SANDBOX_TYPE = "auto"
            mock_settings.APP_ENV = "development"
            mock_settings.E2B_API_KEY = "test-key"
            result = get_sandbox_type()
            assert result == "e2b"


def test_get_sandbox_type_without_e2b_key():
    """测试没有 E2B_API_KEY 时返回 docker"""
    # 保存原始环境变量
    original_key = os.environ.pop("E2B_API_KEY", None)
    try:
        with patch("src.config.settings") as mock_settings:
            mock_settings.SANDBOX_TYPE = "auto"
            mock_settings.APP_ENV = "development"
            mock_settings.E2B_API_KEY = ""
            result = get_sandbox_type()
            assert result == "docker"
    finally:
        # 恢复原始环境变量
        if original_key is not None:
            os.environ["E2B_API_KEY"] = original_key


def test_get_sandbox_type_explicit_docker():
    """测试显式设置 docker"""
    with patch("src.config.settings") as mock_settings:
        mock_settings.SANDBOX_TYPE = "docker"
        mock_settings.APP_ENV = "development"
        result = get_sandbox_type()
        assert result == "docker"


def test_get_sandbox_type_explicit_e2b():
    """测试显式设置 e2b"""
    with patch("src.config.settings") as mock_settings:
        mock_settings.SANDBOX_TYPE = "e2b"
        mock_settings.APP_ENV = "development"
        mock_settings.E2B_API_KEY = "test-key"
        result = get_sandbox_type()
        assert result == "e2b"


def test_hosted_auto_sandbox_fails_closed_without_e2b():
    with patch("src.config.settings") as mock_settings:
        mock_settings.SANDBOX_TYPE = "auto"
        mock_settings.APP_ENV = "production"
        mock_settings.E2B_API_KEY = ""
        with pytest.raises(RuntimeError, match="cannot fall back to Docker"):
            get_sandbox_type()


# ==================== Docker Sandbox 测试 ====================

@pytest.mark.anyio
async def test_docker_sandbox_not_available():
    """测试 Docker 不可用时的错误处理"""
    docker_sandbox = _docker()
    
    # Mock _check_docker_available 方法返回 False
    async def mock_check_docker():
        return False
    
    docker_sandbox._check_docker_available = mock_check_docker
    
    result = await docker_sandbox.start(session_id="s1", data={"a": 1}, readonly=False)
    assert result["success"] is False
    assert "Docker is not available" in result["error"]


@pytest.mark.anyio
async def test_docker_sandbox_session_not_found():
    """测试会话不存在时的错误处理"""
    docker_sandbox = _docker()
    
    result = await docker_sandbox.exec("nonexistent", "echo hello")
    assert result["success"] is False
    assert "session not found" in result["error"].lower()


@pytest.mark.anyio
async def test_docker_sandbox_status_inactive():
    """测试查询不存在的会话状态"""
    docker_sandbox = _docker()
    
    status = await docker_sandbox.status("nonexistent")
    assert status["active"] is False


def test_docker_isolation_contract_is_mandatory():
    args = _docker()._isolation_args()
    assert "--network=none" in args
    user_arg = next(arg for arg in args if arg.startswith("--user="))
    assert user_arg not in {"--user=0", "--user=0:0"}
    assert "--cap-drop=ALL" in args
    assert ["--security-opt", "no-new-privileges"] == args[3:5]
    assert "--read-only" in args
    assert "/tmp:rw,noexec,nosuid,size=64m" in args


def test_docker_identity_matches_non_root_host_owner(monkeypatch):
    monkeypatch.setattr(docker_sandbox_module.os, "name", "posix")
    monkeypatch.setattr(docker_sandbox_module.os, "geteuid", lambda: 1001, raising=False)
    monkeypatch.setattr(docker_sandbox_module.os, "getegid", lambda: 1002, raising=False)
    assert DockerSandbox._container_identity() == "1001:1002"


def test_root_owned_bind_tree_is_transferred_to_fixed_user(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    child = workspace / "data.json"
    child.write_text("{}", encoding="utf-8")
    changed = []
    monkeypatch.setattr(docker_sandbox_module.os, "name", "posix")
    monkeypatch.setattr(docker_sandbox_module.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        docker_sandbox_module.os,
        "chown",
        lambda path, uid, gid: changed.append((os.fspath(path), uid, gid)),
        raising=False,
    )

    DockerSandbox._prepare_bind_mount(os.fspath(workspace))

    assert {item[0] for item in changed} == {os.fspath(workspace), os.fspath(child)}
    assert all(item[1:] == (65532, 65532) for item in changed)


@pytest.mark.anyio
async def test_docker_missing_prebuilt_image_never_uses_network_fallback():
    docker_sandbox = _docker()
    calls = []

    async def fake_docker(*args, **_kwargs):
        calls.append(args)
        return 1, "", "image missing"

    docker_sandbox._run_docker_command = fake_docker
    success, _container, error = await docker_sandbox._try_start_container("test-session", [])
    assert success is False
    assert "Required image json-sandbox:3.19" in error
    assert len(calls) == 1
    flattened = " ".join(calls[0])
    assert "--network=none" in flattened
    assert "alpine:3.19" not in flattened
    assert "apk add" not in flattened


# ==================== 并行下载测试 ====================

@pytest.mark.anyio
async def test_parallel_download():
    """测试并行下载功能"""
    from src.platform.scope_sandbox.execution.file_utils import download_files_parallel
    
    # 创建模拟 S3 服务
    mock_s3 = AsyncMock()
    mock_s3.download_file = AsyncMock(return_value=b"test content")
    mock_s3.get_file_metadata = AsyncMock(return_value=MagicMock(size=100))
    
    files = [
        {"path": "/workspace/file1.txt", "s3_key": "key1"},
        {"path": "/workspace/file2.txt", "s3_key": "key2"},
        {"path": "/workspace/file3.txt", "content": "direct content"},
    ]
    
    results = await download_files_parallel(files, mock_s3, max_concurrent=2)
    
    assert len(results) == 3
    
    # 验证 S3 下载被调用
    assert mock_s3.download_file.call_count == 2
    
    # 验证结果
    paths = [r[0] for r in results]
    assert "/workspace/file1.txt" in paths
    assert "/workspace/file2.txt" in paths
    assert "/workspace/file3.txt" in paths


@pytest.mark.anyio
async def test_prepare_files_for_sandbox():
    """测试准备沙盒文件"""
    from src.platform.scope_sandbox.execution.file_utils import prepare_files_for_sandbox
    
    # 创建模拟 S3 服务
    mock_s3 = AsyncMock()
    mock_s3.download_file = AsyncMock(return_value=b"downloaded content")
    mock_s3.get_file_metadata = AsyncMock(return_value=MagicMock(size=100))
    
    files = [
        {"path": "/workspace/file1.txt", "content": "local content"},
        {"path": "/workspace/file2.txt", "s3_key": "key2"},
    ]
    
    prepared, failed = await prepare_files_for_sandbox(files, mock_s3)
    
    assert len(prepared) == 2
    assert len(failed) == 0
    
    # 验证内容
    file1 = next(f for f in prepared if f["path"] == "/workspace/file1.txt")
    assert file1["content"] == "local content"
    
    file2 = next(f for f in prepared if f["path"] == "/workspace/file2.txt")
    assert file2["content"] == b"downloaded content"


@pytest.mark.anyio
async def test_parallel_download_with_failures():
    """测试并行下载时部分失败"""
    from src.platform.scope_sandbox.execution.file_utils import prepare_files_for_sandbox
    
    # 创建模拟 S3 服务，第二个文件下载失败
    mock_s3 = AsyncMock()
    mock_s3.download_file = AsyncMock(side_effect=[
        b"success content",
        Exception("S3 download failed"),
    ])
    mock_s3.get_file_metadata = AsyncMock(return_value=MagicMock(size=100))
    
    files = [
        {"path": "/workspace/file1.txt", "s3_key": "key1"},
        {"path": "/workspace/file2.txt", "s3_key": "key2"},
    ]
    
    prepared, failed = await prepare_files_for_sandbox(files, mock_s3)
    
    assert len(prepared) == 1
    assert len(failed) == 1
    assert failed[0]["path"] == "/workspace/file2.txt"
    assert "S3 download failed" in failed[0]["error"]
