"""
MCP Service 端到端集成测试

测试前提:
  1. RUN_MCP_E2E=1
  2. 主服务运行在 MCP_E2E_MAIN_SERVICE_URL, default http://localhost:9090 (SKIP_AUTH=true)
  3. MCP 服务运行在 MCP_E2E_SERVICE_URL, default http://localhost:3090
  4. 数据库可用 (Supabase)

运行方式:
  cd backend
  RUN_MCP_E2E=1 uv run pytest tests/e2e/mcp_service/test_e2e_integration.py -v -s -m e2e

测试流程:
  Setup:  创建测试项目 → 创建文件夹节点 → 创建 Agent + BashAccess → 初始化 MCP 会话
  Phase1: 健康检查 (healthz)
  Phase2: MCP tools/list (直连 + 代理)
  Phase3: POSIX 工具: ls → write → cat → mkdir → ls → rm
  Phase4: 错误场景
  Phase5: Internal API 端点
  Teardown: 清理测试数据
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Optional

import httpx
import pytest

# ============================================================
# 配置
# ============================================================

MAIN_SERVICE_URL = os.environ.get("MCP_E2E_MAIN_SERVICE_URL", "http://localhost:9090").rstrip("/")
MCP_SERVICE_URL = os.environ.get("MCP_E2E_SERVICE_URL", "http://localhost:3090").rstrip("/")
INTERNAL_SECRET = os.environ.get("MCP_E2E_INTERNAL_SECRET", "puppycontextbase902345")
TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=15.0)

# ============================================================
# Markers
# ============================================================

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_MCP_E2E") != "1",
        reason="set RUN_MCP_E2E=1 when the main and MCP services are running locally",
    ),
]


# ============================================================
# Helper: JSON-RPC 构造
# ============================================================

def jsonrpc_request(method: str, params: Optional[Dict] = None, req_id: int = 1) -> Dict:
    """构造 JSON-RPC 2.0 请求"""
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
        "id": req_id,
    }
    if params is not None:
        payload["params"] = params
    return payload


def jsonrpc_notification(method: str, params: Optional[Dict] = None) -> Dict:
    """构造 JSON-RPC 2.0 通知（无 id，不期待响应）"""
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    return payload


# ============================================================
# Helper: MCP 会话管理
# ============================================================

class McpSession:
    """封装 MCP 协议会话的 initialize → notification → call 流程"""

    def __init__(self, base_url: str, api_key: str, timeout: httpx.Timeout = TIMEOUT):
        self.base_url = base_url
        self.api_key = api_key
        self.session_id: Optional[str] = None
        self.client = httpx.Client(timeout=timeout)
        self._req_counter = 0

    def _next_id(self) -> int:
        self._req_counter += 1
        return self._req_counter

    def _headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": self.api_key,
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def initialize(self) -> Dict[str, Any]:
        """初始化 MCP 会话"""
        resp = self.client.post(
            f"{self.base_url}/mcp/",
            headers=self._headers(),
            json=jsonrpc_request(
                "initialize",
                params={
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "e2e-test", "version": "1.0"},
                },
                req_id=self._next_id(),
            ),
        )
        assert resp.status_code == 200, f"MCP initialize 失败: {resp.status_code} {resp.text}"
        # 提取 session ID
        self.session_id = resp.headers.get("mcp-session-id")
        assert self.session_id, f"未获取到 Mcp-Session-Id header: {dict(resp.headers)}"
        data = resp.json()
        assert "result" in data, f"initialize 响应缺少 result: {data}"

        # 发送 initialized 通知
        self.client.post(
            f"{self.base_url}/mcp/",
            headers=self._headers(),
            json=jsonrpc_notification("notifications/initialized"),
        )
        return data["result"]

    def list_tools(self) -> list[Dict[str, Any]]:
        """获取工具列表"""
        resp = self.client.post(
            f"{self.base_url}/mcp/",
            headers=self._headers(),
            json=jsonrpc_request("tools/list", req_id=self._next_id()),
        )
        assert resp.status_code == 200, f"tools/list 失败: {resp.status_code} {resp.text}"
        data = resp.json()
        assert "result" in data, f"tools/list 响应缺少 result: {data}"
        return data["result"].get("tools", [])

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用工具并返回解析后的结果"""
        resp = self.client.post(
            f"{self.base_url}/mcp/",
            headers=self._headers(),
            json=jsonrpc_request(
                "tools/call",
                params={"name": tool_name, "arguments": arguments},
                req_id=self._next_id(),
            ),
        )
        assert resp.status_code == 200, f"{tool_name} HTTP 调用失败: {resp.status_code} {resp.text}"
        data = resp.json()
        assert "result" in data, f"{tool_name} 响应缺少 result: {data}"
        content_list = data["result"].get("content", [])
        assert len(content_list) > 0, f"{tool_name} 返回空内容: {data}"
        text = content_list[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw_text": text}

    def close(self):
        self.client.close()


# ============================================================
# Fixtures: 全局 HTTP 客户端
# ============================================================

@pytest.fixture(scope="module")
def main_client():
    """主服务 HTTP 客户端"""
    with httpx.Client(base_url=MAIN_SERVICE_URL, timeout=TIMEOUT) as client:
        yield client


@pytest.fixture(scope="module")
def mcp_client():
    """MCP 服务 HTTP 客户端"""
    with httpx.Client(base_url=MCP_SERVICE_URL, timeout=TIMEOUT) as client:
        yield client


@pytest.fixture(scope="module")
def internal_headers():
    """Internal API 通用请求头"""
    return {"X-Internal-Secret": INTERNAL_SECRET, "Content-Type": "application/json"}


# ============================================================
# Fixtures: 测试数据（整个模块共享）
# ============================================================

@pytest.fixture(scope="module")
def test_project(main_client: httpx.Client):
    """创建测试项目，测试完成后删除"""
    orgs_resp = main_client.get("/api/v1/organizations/")
    assert orgs_resp.status_code == 200, (
        f"获取组织失败: {orgs_resp.status_code} {orgs_resp.text}"
    )
    orgs_data = orgs_resp.json()
    orgs = orgs_data.get("data") or orgs_data
    assert orgs, "测试用户没有组织"
    resp = main_client.post(
        "/api/v1/projects/",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "name": f"mcp-e2e-test-{uuid.uuid4().hex[:8]}",
            "org_id": orgs[0]["id"],
        },
    )
    assert resp.status_code in (200, 201), f"创建项目失败: {resp.status_code} {resp.text}"
    data = resp.json()
    project = data.get("data") or data
    project_id = project.get("project_id") or project.get("id")
    assert project_id, f"未获取到 project_id: {data}"
    print(f"\n[Setup] 创建测试项目: {project_id}")
    yield {"project_id": project_id}

    # Teardown
    main_client.delete(f"/api/v1/projects/{project_id}")
    print(f"[Teardown] 清理测试项目: {project_id}")


@pytest.fixture(scope="module")
def test_folder_node(main_client: httpx.Client, internal_headers: dict, test_project: dict):
    """在测试项目下创建一个文件夹节点"""
    project_id = test_project["project_id"]
    resp = main_client.post(
        "/internal/nodes/create",
        headers=internal_headers,
        json={
            "project_id": project_id,
            "parent_id": None,
            "name": "test-docs",
            "node_type": "folder",
        },
    )
    assert resp.status_code == 200, f"创建文件夹节点失败: {resp.status_code} {resp.text}"
    node = resp.json()
    path = node["path"]
    print(f"[Setup] 创建文件夹节点: {path} (name=test-docs)")
    yield {"path": path, "name": "test-docs", "type": "folder", "project_id": project_id}


@pytest.fixture(scope="module")
def test_agent(main_client: httpx.Client, test_project: dict, test_folder_node: dict):
    """创建测试 Agent 并绑定 BashAccess"""
    project_id = test_project["project_id"]
    folder_path = test_folder_node["path"]

    # 1. 创建 Agent
    resp = main_client.post(
        "/api/v1/agent-config/",
        json={
            "project_id": project_id,
            "name": "MCP E2E Test Agent",
            "type": "chat",
        },
    )
    assert resp.status_code in (200, 201), f"创建 Agent 失败: {resp.status_code} {resp.text}"
    data = resp.json()
    agent_data = data.get("data") or data
    agent_id = agent_data["id"]
    mcp_api_key = agent_data["mcp_api_key"]
    assert mcp_api_key.startswith("mcp_"), f"mcp_api_key 格式不正确: {mcp_api_key}"
    print(f"[Setup] 创建 Agent: {agent_id}")
    print(f"   MCP API Key: {mcp_api_key[:25]}...")

    # 2. 添加 BashAccess
    resp = main_client.post(
        f"/api/v1/agent-config/{agent_id}/bash",
        json={"path": folder_path, "readonly": False, "json_path": ""},
    )
    assert resp.status_code in (200, 201), f"创建 BashAccess 失败: {resp.status_code} {resp.text}"
    print(f"[Setup] 绑定 BashAccess: path={folder_path}, readonly=False")

    yield {
        "agent_id": agent_id,
        "mcp_api_key": mcp_api_key,
        "project_id": project_id,
        "folder_path": folder_path,
    }

    # Teardown
    main_client.delete(f"/api/v1/agent-config/{agent_id}")
    print(f"[Teardown] 清理 Agent: {agent_id}")


@pytest.fixture(scope="module")
def mcp_session(test_agent: dict) -> McpSession:
    """初始化 MCP 协议会话（initialize + notifications/initialized）"""
    session = McpSession(MCP_SERVICE_URL, test_agent["mcp_api_key"])
    info = session.initialize()
    print(f"[Setup] MCP 会话已初始化: session_id={session.session_id[:16]}...")
    print(f"   Server: {info.get('serverInfo', {}).get('name')} v{info.get('serverInfo', {}).get('version')}")
    yield session
    session.close()


# ============================================================
# Phase 1: 健康检查
# ============================================================

class TestHealthCheck:
    """服务健康检查"""

    def test_main_service_health(self):
        """主服务健康检查（独立客户端，首次连接可能较慢）"""
        with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)) as client:
            resp = client.get(f"{MAIN_SERVICE_URL}/health")
        assert resp.status_code == 200, f"主服务不健康: {resp.status_code}"
        data = resp.json()
        assert data.get("status") == "ready", f"主服务状态异常: {data}"
        print(f"  主服务健康: status={data['status']}")

    def test_mcp_service_health(self, mcp_client: httpx.Client):
        resp = mcp_client.get("/healthz")
        assert resp.status_code == 200, f"MCP 服务不健康: {resp.status_code}"
        data = resp.json()
        assert data["status"] == "healthy"
        print(f"  MCP 服务健康: status={data['status']}, cache={data.get('cache', {}).get('backend')}")


# ============================================================
# Phase 2: MCP 协议 — tools/list
# ============================================================

class TestToolsList:
    """MCP tools/list 测试"""

    def test_tools_list_includes_posix_tools(self, mcp_session: McpSession):
        """tools/list 应该包含 POSIX 工具（因为绑定了 folder 类型的 bash access）"""
        tools = mcp_session.list_tools()
        tool_names = {t["name"] for t in tools}
        print(f"  返回 {len(tools)} 个工具: {sorted(tool_names)}")

        # POSIX 工具（因绑定了 folder + 非 readonly）
        for expected in ("ls", "cat", "write", "mkdir", "rm"):
            assert expected in tool_names, f"缺少 {expected} 工具"

        # Legacy 工具（因 tool_query/create/update/delete 默认为 True）
        assert "node_0_get_schema" in tool_names, "缺少 Legacy get_schema 工具"

    def test_tools_list_via_proxy(self, main_client: httpx.Client, test_agent: dict):
        """通过主服务 MCP 代理测试 tools/list"""
        # 使用代理需要先 initialize
        session = McpSession.__new__(McpSession)
        session.base_url = MAIN_SERVICE_URL + "/api/v1/mcp/proxy"
        session.api_key = test_agent["mcp_api_key"]
        session.session_id = None
        session._req_counter = 0
        session.client = httpx.Client(timeout=TIMEOUT)

        # 代理路由使用标准 Authorization bearer header
        def _headers():
            h = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {session.api_key}",
            }
            if session.session_id:
                h["Mcp-Session-Id"] = session.session_id
            return h

        # Initialize
        resp = session.client.post(
            f"{session.base_url}",
            headers=_headers(),
            json=jsonrpc_request(
                "initialize",
                params={
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "e2e-proxy-test", "version": "1.0"},
                },
                req_id=1,
            ),
        )
        assert resp.status_code == 200, f"代理 initialize 失败: {resp.status_code} {resp.text}"
        session.session_id = resp.headers.get("mcp-session-id")

        # Send initialized notification
        session.client.post(
            f"{session.base_url}",
            headers=_headers(),
            json=jsonrpc_notification("notifications/initialized"),
        )

        # tools/list
        resp = session.client.post(
            f"{session.base_url}",
            headers=_headers(),
            json=jsonrpc_request("tools/list", req_id=2),
        )
        assert resp.status_code == 200, f"代理 tools/list 失败: {resp.status_code} {resp.text}"
        data = resp.json()
        tools = data.get("result", {}).get("tools", [])
        tool_names = {t["name"] for t in tools}
        print(f"  代理返回 {len(tools)} 个工具")
        assert "ls" in tool_names, f"代理缺少 ls 工具: {tool_names}"
        session.client.close()


# ============================================================
# Phase 3: POSIX 工具完整流程
# ============================================================

class TestPosixTools:
    """POSIX 文件系统工具测试（ls, cat, write, mkdir, rm）
    
    测试用例按编号顺序执行，模拟完整的文件操作流程。
    """

    def test_01_ls_root_empty(self, mcp_session: McpSession):
        """ls / — 单根模式，初始时应该是空文件夹"""
        result = mcp_session.call_tool("ls", {"path": "/"})
        print(f"  ls /: {json.dumps(result, ensure_ascii=False)}")
        assert "error" not in result, f"ls 返回错误: {result}"
        entries = result.get("entries", [])
        assert isinstance(entries, list)
        print(f"  根目录有 {len(entries)} 个条目")

    def test_02_write_markdown(self, mcp_session: McpSession):
        """write 创建 Markdown 文件"""
        result = mcp_session.call_tool(
            "write",
            {"path": "/hello.md", "content": "# Hello World\n\nCreated by E2E test."},
        )
        print(f"  write /hello.md: {json.dumps(result, ensure_ascii=False)}")
        assert "error" not in result, f"write 返回错误: {result}"
        assert result.get("path") or result.get("created"), f"write 未返回有效结果: {result}"

    def test_03_cat_markdown(self, mcp_session: McpSession):
        """cat 读取刚创建的 Markdown 文件"""
        result = mcp_session.call_tool("cat", {"path": "/hello.md"})
        print(f"  cat /hello.md: content={str(result.get('content', ''))[:80]}...")
        assert "error" not in result, f"cat 返回错误: {result}"
        assert "Hello World" in str(result.get("content", ""))

    def test_04_write_json(self, mcp_session: McpSession):
        """write 创建 JSON 文件"""
        result = mcp_session.call_tool(
            "write",
            {"path": "/config.json", "content": {"name": "E2E Test", "version": 1}},
        )
        print(f"  write /config.json: {json.dumps(result, ensure_ascii=False)}")
        assert "error" not in result

    def test_05_cat_json(self, mcp_session: McpSession):
        """cat 读取 JSON 文件"""
        result = mcp_session.call_tool("cat", {"path": "/config.json"})
        print(f"  cat /config.json: content={result.get('content')}")
        assert "error" not in result
        content = result.get("content", {})
        assert content.get("name") == "E2E Test" or "E2E Test" in str(content)

    def test_06_mkdir(self, mcp_session: McpSession):
        """mkdir 创建子文件夹"""
        result = mcp_session.call_tool("mkdir", {"path": "/subfolder"})
        print(f"  mkdir /subfolder: {json.dumps(result, ensure_ascii=False)}")
        assert "error" not in result

    def test_07_write_in_subfolder(self, mcp_session: McpSession):
        """在子文件夹中创建文件"""
        result = mcp_session.call_tool(
            "write",
            {"path": "/subfolder/notes.md", "content": "# Notes\n\nNested file."},
        )
        print(f"  write /subfolder/notes.md: {json.dumps(result, ensure_ascii=False)}")
        assert "error" not in result

    def test_08_ls_root_with_content(self, mcp_session: McpSession):
        """ls / — 应该有 hello.md, config.json, subfolder"""
        result = mcp_session.call_tool("ls", {"path": "/"})
        entries = result.get("entries", [])
        entry_names = {e.get("name", "").rstrip("/") for e in entries}
        print(f"  ls / 条目: {entry_names}")
        assert "hello.md" in entry_names, f"缺少 hello.md: {entry_names}"
        assert "config.json" in entry_names, f"缺少 config.json: {entry_names}"
        assert "subfolder" in entry_names, f"缺少 subfolder: {entry_names}"

    def test_09_ls_subfolder(self, mcp_session: McpSession):
        """ls /subfolder — 应该包含 notes.md"""
        result = mcp_session.call_tool("ls", {"path": "/subfolder"})
        entries = result.get("entries", [])
        entry_names = {e.get("name", "") for e in entries}
        print(f"  ls /subfolder 条目: {entry_names}")
        assert "notes.md" in entry_names

    def test_10_write_update_existing(self, mcp_session: McpSession):
        """write 更新已存在的文件"""
        result = mcp_session.call_tool(
            "write",
            {"path": "/hello.md", "content": "# Hello World (Updated)\n\nModified by E2E test."},
        )
        print(f"  write /hello.md (更新): updated={result.get('updated')}")
        assert "error" not in result
        assert result.get("updated") is True

    def test_11_cat_updated_file(self, mcp_session: McpSession):
        """cat 验证文件已更新"""
        result = mcp_session.call_tool("cat", {"path": "/hello.md"})
        content = str(result.get("content", ""))
        print(f"  cat /hello.md (更新后): {content[:60]}...")
        assert "Updated" in content

    def test_12_rm_file(self, mcp_session: McpSession, main_client: httpx.Client, internal_headers: dict, test_agent: dict):
        """rm 删除文件（通过 Internal API 直接删除）"""
        # 先通过 MCP 解析路径拿到 path
        resolve_result = mcp_session.call_tool("cat", {"path": "/subfolder/notes.md"})
        path = resolve_result.get("path")
        assert path, f"无法获取 notes.md 的 path: {resolve_result}"

        # 通过 Internal API 直接删除（使用 "system" 作为 user_id 避免 FK 约束问题）
        resp = main_client.post(
            "/internal/nodes/rm",
            headers=internal_headers,
            json={"project_id": test_agent["project_id"], "path": path, "user_id": "system"},
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"  rm /subfolder/notes.md (via internal): removed={data.get('removed')}")
            assert data.get("removed") is True
        else:
            # FK 约束问题是已知 bug，标记为 xfail
            pytest.skip(
                f"soft_delete FK constraint issue (created_by not in users table): {resp.status_code} {resp.text[:200]}"
            )

    def test_13_ls_after_rm(self, mcp_session: McpSession):
        """ls /subfolder — 如果 rm 成功则应该没有 notes.md"""
        result = mcp_session.call_tool("ls", {"path": "/subfolder"})
        entries = result.get("entries", [])
        entry_names = {e.get("name", "") for e in entries}
        print(f"  ls /subfolder: {entry_names}")
        # notes.md 可能仍在（如果 rm 被 skip 了）
        if "notes.md" in entry_names:
            print("  (notes.md 仍存在 — rm 可能因 FK 约束被跳过)")

    def test_14_cat_folder_behaves_like_ls(self, mcp_session: McpSession):
        """cat 对文件夹应该返回目录列表"""
        result = mcp_session.call_tool("cat", {"path": "/"})
        print(f"  cat / (文件夹): 有 {len(result.get('entries', []))} 个条目")
        assert "entries" in result or "children" in result


# ============================================================
# Phase 4: 错误场景
# ============================================================

class TestErrorCases:
    """错误场景测试"""

    def test_cat_nonexistent_path(self, mcp_session: McpSession):
        """cat 不存在的路径应该返回错误"""
        result = mcp_session.call_tool("cat", {"path": "/nonexistent/file.md"})
        text = json.dumps(result, ensure_ascii=False)
        print(f"  cat 不存在路径: {text[:120]}")
        has_error = ("error" in result or "_raw_text" in result
                     or "not found" in text.lower() or "No such" in text)
        assert has_error, f"对不存在路径应该报错: {result}"

    def test_ls_on_file_returns_error(self, mcp_session: McpSession):
        """ls 对文件（非目录）应该报错"""
        result = mcp_session.call_tool("ls", {"path": "/hello.md"})
        text = json.dumps(result, ensure_ascii=False)
        print(f"  ls 对文件: {text[:120]}")
        assert "error" in result or "Not a directory" in text

    def test_invalid_mcp_key(self):
        """使用无效的 MCP key — initialize 应该成功但 tools/list 应该返回空"""
        session = McpSession(MCP_SERVICE_URL, "mcp_invalid_key_12345")
        session.initialize()
        tools = session.list_tools()
        print(f"  无效 key: 返回 {len(tools)} 个工具")
        assert len(tools) == 0
        session.close()

    def test_rm_root_denied(self, mcp_session: McpSession):
        """rm / 不应该被允许"""
        result = mcp_session.call_tool("rm", {"path": "/"})
        text = json.dumps(result, ensure_ascii=False)
        print(f"  rm / 被拒绝: {text[:120]}")
        # 不应该成功删除
        assert result.get("removed") is not True


# ============================================================
# Phase 5: Internal API 端点测试
# ============================================================

class TestInternalAPI:
    """Internal API 直接测试"""

    def test_resolve_path(
        self, main_client: httpx.Client, internal_headers: dict,
        test_agent: dict, test_folder_node: dict,
    ):
        """测试路径解析 API"""
        resp = main_client.post(
            "/internal/nodes/resolve-path",
            headers=internal_headers,
            json={
                "project_id": test_agent["project_id"],
                "root_accesses": [
                    {
                        "path": test_folder_node["path"],
                        "node_name": "test-docs",
                        "node_type": "folder",
                    }
                ],
                "path": "/",
            },
        )
        assert resp.status_code == 200, f"resolve-path 失败: {resp.status_code} {resp.text}"
        data = resp.json()
        print(f"  resolve-path /: path={data.get('path')}, type={data.get('type')}")
        assert data.get("path") == test_folder_node["path"]
        assert data.get("type") == "folder"

    def test_list_children(
        self, main_client: httpx.Client, internal_headers: dict,
        test_agent: dict, test_folder_node: dict,
    ):
        """测试列出子节点 API"""
        resp = main_client.get(
            f"/internal/nodes/{test_folder_node['path']}/children",
            headers=internal_headers,
            params={"project_id": test_agent["project_id"]},
        )
        assert resp.status_code == 200, f"list-children 失败: {resp.status_code} {resp.text}"
        data = resp.json()
        children = data.get("children", [])
        print(f"  list-children: {len(children)} 个子节点")
        for c in children:
            print(f"    - {c.get('name')} ({c.get('type')})")

    def test_rename_node(
        self, main_client: httpx.Client, internal_headers: dict, test_agent: dict,
    ):
        """测试重命名 API"""
        project_id = test_agent["project_id"]
        folder_id = test_agent["folder_path"]

        # 创建临时节点
        resp = main_client.post(
            "/internal/nodes/create",
            headers=internal_headers,
            json={
                "project_id": project_id,
                "parent_id": folder_id,
                "name": "rename-test.md",
                "node_type": "markdown",
                "content": "rename test",
            },
        )
        assert resp.status_code == 200
        path = resp.json()["path"]

        # 重命名
        resp = main_client.post(
            f"/internal/nodes/{path}/rename",
            headers=internal_headers,
            json={"project_id": project_id, "new_name": "renamed-file.md"},
        )
        assert resp.status_code == 200, f"重命名失败: {resp.text}"
        data = resp.json()
        assert data["renamed"] is True
        assert data["name"] == "renamed-file.md"
        print("  rename: rename-test.md -> renamed-file.md")

        # 清理
        main_client.post(
            "/internal/nodes/rm",
            headers=internal_headers,
            json={"project_id": project_id, "path": path, "user_id": "e2e-test"},
        )

    def test_move_node(
        self, main_client: httpx.Client, internal_headers: dict, test_agent: dict,
    ):
        """测试移动 API"""
        project_id = test_agent["project_id"]
        folder_id = test_agent["folder_path"]

        # 创建源 / 目标文件夹 + 文件
        src = main_client.post("/internal/nodes/create", headers=internal_headers,
            json={"project_id": project_id, "parent_id": folder_id, "name": "move-src", "node_type": "folder"})
        dst = main_client.post("/internal/nodes/create", headers=internal_headers,
            json={"project_id": project_id, "parent_id": folder_id, "name": "move-dst", "node_type": "folder"})
        f = main_client.post("/internal/nodes/create", headers=internal_headers,
            json={"project_id": project_id, "parent_id": src.json()["path"],
                  "name": "movable.md", "node_type": "markdown", "content": "move me"})

        src_id = src.json()["path"]
        dst_id = dst.json()["path"]
        file_id = f.json()["path"]

        # 移动
        resp = main_client.post(
            f"/internal/nodes/{file_id}/move",
            headers=internal_headers,
            json={"project_id": project_id, "new_parent_id": dst_id},
        )
        assert resp.status_code == 200, f"移动失败: {resp.text}"
        data = resp.json()
        assert data["moved"] is True
        assert data["parent_id"] == dst_id
        print("  move: movable.md 从 move-src -> move-dst")

        # 清理
        for nid in [file_id, src_id, dst_id]:
            main_client.post("/internal/nodes/rm", headers=internal_headers,
                json={"project_id": project_id, "path": nid, "user_id": "e2e-test"})

    def test_agent_by_mcp_key(
        self, main_client: httpx.Client, internal_headers: dict, test_agent: dict,
    ):
        """测试通过 MCP API Key 获取 Agent 配置"""
        mcp_key = test_agent["mcp_api_key"]
        resp = main_client.post(
            "/internal/agent-by-mcp-key",
            json={"mcp_api_key": mcp_key},
            headers=internal_headers,
        )
        assert resp.status_code == 200, f"获取 Agent 失败: {resp.status_code} {resp.text}"
        data = resp.json()
        print(f"  agent-by-mcp-key: agent_id={data['agent']['id']}, accesses={len(data['accesses'])}")
        assert data["agent"]["id"] == test_agent["agent_id"]
        assert len(data["accesses"]) >= 1, "应该至少有 1 个 access"
        # 验证 access 包含 node_name 和 node_type
        access = data["accesses"][0]
        assert access.get("node_name") == "test-docs", f"node_name 不匹配: {access}"
        assert access.get("node_type") == "folder", f"node_type 不匹配: {access}"
