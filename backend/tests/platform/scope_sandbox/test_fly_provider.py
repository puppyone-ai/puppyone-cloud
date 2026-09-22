"""FlyMachinesProvider tests with a mocked httpx transport."""

from __future__ import annotations

import json

import httpx
import pytest

from src.platform.scope_sandbox.fly_provider import FlyMachinesProvider
from src.platform.scope_sandbox.provider import SandboxSpec, SandboxState


def _provider(handler):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.machines.dev",
    )
    return FlyMachinesProvider("myapp", "tok", client=client, default_image="img:latest")


def _spec():
    return SandboxSpec(scope_id="s1", project_id="p1", vcpus=2, memory_mb=2048, region="ams")


async def test_create_posts_machine_and_returns_running():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "m1", "instance_id": "i1", "state": "started"})

    info = await _provider(handler).create(_spec())

    assert info.sandbox_id == "m1"
    assert info.state is SandboxState.RUNNING
    assert info.connection.host == "myapp.fly.dev" and info.connection.port == 22

    req = seen[0]
    assert req.method == "POST"
    assert req.url.path == "/v1/apps/myapp/machines"
    assert req.headers["authorization"] == "Bearer tok"
    body = json.loads(req.content)
    assert body["region"] == "ams"
    assert body["config"]["image"] == "img:latest"
    assert body["config"]["guest"]["cpus"] == 2
    # sshd internal 2222 exposed as public TCP 22
    svc = body["config"]["services"][0]
    assert svc["protocol"] == "tcp" and svc["internal_port"] == 2222
    assert svc["ports"][0]["port"] == 22


async def test_create_waits_when_not_started():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/wait"):
            assert request.url.params["state"] == "started"
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"id": "m1", "instance_id": "i1", "state": "created"})

    info = await _provider(handler).create(_spec())
    assert info.state is SandboxState.RUNNING
    assert any(p.endswith("/wait") for p in paths)  # waited for "started"


async def test_start_stop_destroy_hit_right_endpoints():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    prov = _provider(handler)
    assert (await prov.start("m1")).state is SandboxState.RUNNING
    assert (await prov.stop("m1")).state is SandboxState.STOPPED
    await prov.destroy("m1")

    assert ("POST", "/v1/apps/myapp/machines/m1/start") in seen
    assert ("POST", "/v1/apps/myapp/machines/m1/stop") in seen
    assert ("DELETE", "/v1/apps/myapp/machines/m1") in seen


@pytest.mark.parametrize(
    "fly_state,expected",
    [
        ("started", SandboxState.RUNNING),
        ("stopped", SandboxState.STOPPED),
        ("suspended", SandboxState.STOPPED),
        ("destroyed", SandboxState.DESTROYED),
        ("starting", SandboxState.PENDING),
        ("weird", SandboxState.UNKNOWN),
    ],
)
async def test_status_maps_states(fly_state, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "m1", "state": fly_state})

    assert (await _provider(handler).status("m1")).state is expected


async def test_status_404_is_destroyed_and_destroy_404_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    prov = _provider(handler)
    assert (await prov.status("gone")).state is SandboxState.DESTROYED
    await prov.destroy("gone")  # 404 on delete must not raise


async def test_start_retries_after_412_while_still_stopping():
    # Fly stop is async: a start while the machine is still `stopping` returns
    # 412. The provider must wait for `stopped` then retry the start.
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/start"):
            events.append("start")
            # first start 412s (still stopping); second succeeds
            if events.count("start") == 1:
                return httpx.Response(412, json={"error": "machine not in stopped state"})
            return httpx.Response(200, json={})
        if path.endswith("/wait"):
            events.append(f"wait:{request.url.params.get('state')}")
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={})

    info = await _provider(handler).start("m1")
    assert info.state is SandboxState.RUNNING
    assert events == ["start", "wait:stopped", "start"]  # 412 → wait stopped → retry


async def test_start_propagates_non_412_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(httpx.HTTPStatusError):
        await _provider(handler).start("m1")


async def test_exec_posts_command_as_ssh_user_and_returns_result():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"stdout": "hi\n", "stderr": "", "exit_code": 0})

    out = await _provider(handler).exec("m1", "echo hi")
    assert out == {"stdout": "hi\n", "stderr": "", "exit_code": 0}

    req = seen[0]
    assert req.method == "POST" and req.url.path == "/v1/apps/myapp/machines/m1/exec"
    body = json.loads(req.content)
    # runs through a shell, re-entered as the SSH user so ~ / ownership match
    assert body["command"][0] == "/bin/sh" and body["command"][1] == "-c"
    assert "su - puppy -c" in body["command"][2] and "echo hi" in body["command"][2]


async def test_exec_raises_on_nonzero_exit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stdout": "", "stderr": "boom", "exit_code": 1})

    with pytest.raises(RuntimeError, match="fly exec failed"):
        await _provider(handler).exec("m1", "false")


async def test_secret_write_uses_stdin_not_command_arguments():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"stdout": "", "stderr": "", "exit_code": 0})

    secret = "git-token-never-in-command"
    await _provider(handler).write_secret(
        "m1", ".config/puppyone/git-http-token", secret
    )
    body = json.loads(seen[0].content)
    assert body["stdin"] == secret
    assert secret not in " ".join(body["command"])
    assert "git-http-token" in " ".join(body["command"])


async def test_exec_single_quote_in_command_is_escaped():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"stdout": "", "stderr": "", "exit_code": 0})

    await _provider(handler).exec("m1", "git config user.name 'Al O''Brien'")
    cmd = json.loads(seen[0].content)["command"][2]
    # the su -c wrapper stays balanced: every embedded ' is '\'' escaped
    assert "'\\''" in cmd


def test_capabilities():
    caps = _provider(lambda r: httpx.Response(200, json={})).capabilities()
    assert caps.name == "fly"
    assert caps.supports_stop_resume and caps.supports_tcp_ingress
    assert not caps.self_hostable
    # Fly exec is SSH-based: a setsid&-detached command survives, so the sidecar
    # is started in-line (self-detach), NOT via background mode (that's E2B).
    assert not caps.background_exec_required


async def test_install_and_start_sidecar_command_survives_su_wrapping():
    """install_and_start over the REAL Fly provider (#10 prep): the self-detach
    sidecar start (setsid + single-quoted SYNC_* env) must survive the nested
    `su - puppy -c '…'` quoting intact. This is the Fly path I can't run live until
    the app has a public IPv4 — pin the exact command shape here instead."""
    from src.platform.scope_sync.sidecar_provision import build_sidecar_env, install_and_start

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"stdout": "", "stderr": "", "exit_code": 0})

    env = build_sidecar_env(
        {"checkpoint_debounce_s": 5.0, "quiescence_publish_s": 0.0, "conflict_policy": "agent_review"},
        repo_dir="/home/puppy/u1", events_url="https://q/api/v1/scope-sync/ap/events",
        project_id="p1", scope_id="s1", token="cli_KEY123",
    )
    await install_and_start(_provider(handler), "m1", repo_dir="/home/puppy/u1",
                            env=env, script_text="x=1\n")

    exec_cmds = [json.loads(r.content)["command"][2] for r in seen if r.url.path.endswith("/exec")]
    assert len(exec_cmds) == 2                          # Fly: install + ONE self-detach start
    start = exec_cmds[1]
    assert "su - puppy -c" in start                     # re-entered as the SSH user
    assert "setsid python3" in start and "watch" in start and "echo sidecar-started" in start
    # env values survive the _shq + su nested escaping
    assert "/home/puppy/u1" in start and "cli_KEY123" in start
    assert "'\\''" in start                             # quoting stayed balanced
