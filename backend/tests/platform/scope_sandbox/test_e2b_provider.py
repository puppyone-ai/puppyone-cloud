"""E2BProvider tests with a fake E2BClient + manager wiring for both providers."""

from __future__ import annotations

import httpx

from src.platform.scope_sandbox.e2b_provider import E2BClient, E2BProvider
from src.platform.scope_sandbox.fly_provider import FlyMachinesProvider
from src.platform.scope_sandbox.manager import AcquiredVia, ScopeSandboxManager
from src.platform.scope_sandbox.policy import SessionPolicyConfig
from src.platform.scope_sandbox.provider import SandboxSpec, SandboxState
from src.platform.scope_sandbox.registry import InMemorySandboxSessionStore


class FakeE2BClient(E2BClient):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._n = 0
        self.states: dict[str, SandboxState] = {}
        self.files: dict[tuple[str, str], str] = {}

    def create(self, spec: SandboxSpec) -> str:
        self._n += 1
        sid = f"e2b-{self._n}"
        self.calls.append(("create", sid))
        self.states[sid] = SandboxState.RUNNING
        return sid

    def pause(self, sandbox_id: str) -> None:
        self.calls.append(("pause", sandbox_id))
        self.states[sandbox_id] = SandboxState.STOPPED

    def resume(self, sandbox_id: str) -> None:
        self.calls.append(("resume", sandbox_id))
        self.states[sandbox_id] = SandboxState.RUNNING

    def kill(self, sandbox_id: str) -> None:
        self.calls.append(("kill", sandbox_id))
        self.states[sandbox_id] = SandboxState.DESTROYED

    def get_state(self, sandbox_id: str) -> SandboxState:
        return self.states.get(sandbox_id, SandboxState.UNKNOWN)

    def exec(self, sandbox_id: str, command: str, background: bool = False) -> dict:
        self.calls.append(("exec", sandbox_id, background))
        return {"exit_code": 0, "stdout": f"ran:{command}", "stderr": ""}

    def set_timeout(self, sandbox_id: str) -> None:
        self.calls.append(("set_timeout", sandbox_id))

    def write_file(self, sandbox_id: str, path: str, value: str) -> None:
        self.calls.append(("write_file", sandbox_id, path))
        self.files[(sandbox_id, path)] = value

    def count(self, op: str) -> int:
        return sum(1 for c in self.calls if c[0] == op)


def _spec(scope="s1"):
    return SandboxSpec(scope_id=scope, project_id="p1")


async def test_e2b_lifecycle_maps_to_pause_resume_kill():
    client = FakeE2BClient()
    prov = E2BProvider(client)

    created = await prov.create(_spec())
    assert created.state is SandboxState.RUNNING and client.count("create") == 1

    stopped = await prov.stop(created.sandbox_id)
    assert stopped.state is SandboxState.STOPPED and client.count("pause") == 1

    started = await prov.start(created.sandbox_id)
    assert started.state is SandboxState.RUNNING and client.count("resume") == 1

    await prov.destroy(created.sandbox_id)
    assert client.count("kill") == 1

    assert (await prov.status(created.sandbox_id)).state is SandboxState.DESTROYED


async def test_e2b_exec_delegates_to_client():
    client = FakeE2BClient()
    prov = E2BProvider(client)
    created = await prov.create(_spec())
    out = await prov.exec(created.sandbox_id, "echo hi")
    assert out["exit_code"] == 0 and "echo hi" in out["stdout"]
    assert client.count("exec") == 1


async def test_e2b_extend_calls_set_timeout():
    client = FakeE2BClient()
    prov = E2BProvider(client)
    created = await prov.create(_spec())
    await prov.extend(created.sandbox_id)
    assert client.count("set_timeout") == 1


async def test_e2b_secret_uses_file_channel_not_exec_command():
    client = FakeE2BClient()
    prov = E2BProvider(client)
    created = await prov.create(_spec())
    await prov.write_secret(created.sandbox_id, ".config/puppyone/token", "secret")
    assert client.files[(created.sandbox_id, "/home/user/.config/puppyone/token")] == "secret"
    assert client.count("exec") == 0


async def test_e2b_connection_is_wss_tunnel_not_native_tcp():
    info = await E2BProvider(FakeE2BClient()).create(_spec())
    conn = info.connection
    assert conn.proxy_command and "websocat" in conn.proxy_command
    assert info.sandbox_id in conn.extra["wss_url"]


async def test_e2b_connection_login_user_matches_e2b_account():
    # The E2B template's account is "user"; authorized_keys + the scope
    # workspace live under /home/user, so the SSH login name must be "user"
    # (a "puppy" default would break VSCode Remote-SSH on E2B).
    from src.platform.scope_sandbox.ssh_e2b import DEFAULT_USER
    info = await E2BProvider(FakeE2BClient()).create(_spec())
    assert info.connection.username == "user" == DEFAULT_USER


def test_e2b_capabilities():
    caps = E2BProvider(FakeE2BClient()).capabilities()
    assert caps.name == "e2b"
    assert caps.supports_stop_resume and caps.self_hostable
    assert not caps.supports_tcp_ingress   # SSH must be tunnelled
    assert caps.background_exec_required    # long-runners need exec(background=True)


# ── both providers plug into the manager (the two selectable versions) ──

_CFG = SessionPolicyConfig(
    base_idle_stop_s=100, idle_destroy_s=1000,
    per_recent_user_bonus_s=0, repo_pull_weight=0, hot_warm_bonus_s=0,
)


async def test_e2b_through_manager_warm_cold_cycle():
    client = FakeE2BClient()
    mgr = ScopeSandboxManager(E2BProvider(client), InMemorySandboxSessionStore(), _CFG)

    assert (await mgr.acquire(_spec(), "u", now=0)).via is AcquiredVia.CREATED
    await mgr.release("s1", "u", now=0)
    await mgr.reap(now=200)                       # idle → STOP → pause
    assert client.count("pause") == 1
    assert (await mgr.acquire(_spec(), "u", now=300)).via is AcquiredVia.RESUMED
    assert client.count("resume") == 1
    assert client.count("create") == 1            # never re-created (warm/stopped reuse)


async def test_fly_through_manager_warm_cold_cycle():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/machines"):     # create
            return httpx.Response(200, json={"id": "m1", "instance_id": "i1", "state": "started"})
        return httpx.Response(200, json={})            # start/stop/destroy

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.machines.dev")
    prov = FlyMachinesProvider("app", "tok", client=client, default_image="img")
    mgr = ScopeSandboxManager(prov, InMemorySandboxSessionStore(), _CFG)

    assert (await mgr.acquire(_spec(), "u", now=0)).via is AcquiredVia.CREATED
    await mgr.release("s1", "u", now=0)
    summary = await mgr.reap(now=200)              # idle → STOP (fly stop, disk kept)
    assert summary.stopped == 1
    assert (await mgr.acquire(_spec(), "u", now=300)).via is AcquiredVia.RESUMED
