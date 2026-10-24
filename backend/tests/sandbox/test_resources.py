"""Identity-only destroy/absence checks used by Project deletion and retirement."""

import asyncio

import httpx
import pytest

from src.config import settings
from src.platform.sandbox import destroy_resource, resource_absent
from src.platform.sandbox import resources as sandbox_resources
from src.platform.sandbox.providers.docker import DockerRuntimeProvider
from src.platform.sandbox.providers.e2b import E2BRuntimeProvider
from src.platform.sandbox.providers.fly import FlyRuntimeProvider

# ── package entry ──────────────────────────────────────────────────────────


async def test_entry_dispatches_to_the_named_provider_and_closes_it(monkeypatch):
    calls = []

    class Provider:
        async def destroy_resource(self, resource_id, *, pool):
            calls.append(("destroy", resource_id, pool))

        async def resource_absent(self, resource_id, *, pool):
            calls.append(("absent", resource_id, pool))
            return True

        async def aclose(self):
            calls.append(("aclose",))

    names = []
    monkeypatch.setattr(sandbox_resources, "runtime_provider", lambda name: names.append(name) or Provider())

    await destroy_resource("e2b", "sbx-1", pool="legacy_scope")
    assert await resource_absent("fly", "m-1") is True

    assert names == ["e2b", "fly"]
    assert calls == [
        ("destroy", "sbx-1", "legacy_scope"), ("aclose",),
        ("absent", "m-1", "runtime"), ("aclose",),
    ]


@pytest.mark.parametrize("call", [destroy_resource, resource_absent])
async def test_unknown_pool_or_provider_fails_closed(call):
    with pytest.raises(ValueError):
        await call("e2b", "sbx-1", pool="other")
    with pytest.raises(ValueError):
        await call("unknown-provider", "sbx-1")


# ── docker ─────────────────────────────────────────────────────────────────


def _docker(monkeypatch, *, returncode=0, stderr=b""):
    commands = []

    class Process:
        def __init__(self):
            self.returncode = returncode

        async def communicate(self):
            return b"", stderr

        async def wait(self):
            return returncode

    async def create_subprocess_exec(*args, **_kwargs):
        commands.append(args)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    return commands


async def test_docker_destroy_removes_the_container_and_tolerates_absence(monkeypatch):
    commands = _docker(monkeypatch, returncode=1, stderr=b"Error: No such container: ctr-1")
    await DockerRuntimeProvider().destroy_resource("ctr-1")
    assert commands == [("docker", "rm", "-f", "ctr-1")]


async def test_docker_destroy_failure_is_raised(monkeypatch):
    _docker(monkeypatch, returncode=1, stderr=b"daemon unavailable")
    with pytest.raises(RuntimeError):
        await DockerRuntimeProvider().destroy_resource("ctr-1")


@pytest.mark.parametrize(("returncode", "absent"), [(0, False), (1, True)])
async def test_docker_absence_is_a_failed_inspect(monkeypatch, returncode, absent):
    commands = _docker(monkeypatch, returncode=returncode)
    assert await DockerRuntimeProvider().resource_absent("ctr-1") is absent
    assert commands == [("docker", "inspect", "ctr-1")]


# ── e2b ────────────────────────────────────────────────────────────────────


async def test_e2b_destroy_kills_by_id_in_every_pool(monkeypatch):
    from e2b import AsyncSandbox

    killed = []

    async def kill(resource_id, **options):
        killed.append((resource_id, options.get("api_key")))
        return False  # already gone is success

    monkeypatch.setattr(settings, "E2B_API_KEY", "e2b-key")
    monkeypatch.setattr(AsyncSandbox, "kill", kill)
    await E2BRuntimeProvider().destroy_resource("sbx-1", pool="legacy_scope")
    assert killed == [("sbx-1", "e2b-key")]


async def test_e2b_absence_requires_a_definite_not_found(monkeypatch):
    from e2b import AsyncSandbox
    from e2b.exceptions import SandboxNotFoundException

    outcome = {}

    async def get_info(resource_id, **_options):
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return object()

    monkeypatch.setattr(AsyncSandbox, "get_info", get_info)
    provider = E2BRuntimeProvider()

    outcome["raise"] = SandboxNotFoundException("gone")
    assert await provider.resource_absent("sbx-1") is True
    outcome["raise"] = None
    assert await provider.resource_absent("sbx-1") is False
    # A transport/API failure is not proof of absence; deletion must retry.
    outcome["raise"] = RuntimeError("api unavailable")
    with pytest.raises(RuntimeError):
        await provider.resource_absent("sbx-1")


# ── fly ────────────────────────────────────────────────────────────────────


def _fly(monkeypatch, handler):
    requests = []

    def record(request):
        requests.append(
            (request.method, request.url.path, request.url.query.decode(),
             request.headers.get("authorization"))
        )
        return handler(request)

    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_APP", "agent-app")
    monkeypatch.setattr(settings, "CLOUD_AGENT_FLY_TOKEN", "agent-token")
    monkeypatch.setattr(settings, "SCOPE_SANDBOX_FLY_APP", "scope-app")
    monkeypatch.setattr(settings, "SCOPE_SANDBOX_FLY_TOKEN", "scope-token")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(record), base_url="https://api.machines.dev"
    )
    return FlyRuntimeProvider(client=client), requests


@pytest.mark.parametrize(
    ("pool", "app", "token"),
    [("runtime", "agent-app", "agent-token"), ("legacy_scope", "scope-app", "scope-token")],
)
async def test_fly_destroy_uses_the_pool_account(monkeypatch, pool, app, token):
    provider, requests = _fly(monkeypatch, lambda _request: httpx.Response(200, json={}))
    await provider.destroy_resource("m-1", pool=pool)
    assert requests == [
        ("DELETE", f"/v1/apps/{app}/machines/m-1", "force=true", f"Bearer {token}")
    ]


async def test_fly_destroy_tolerates_a_missing_machine(monkeypatch):
    provider, _ = _fly(monkeypatch, lambda _request: httpx.Response(404, json={}))
    await provider.destroy_resource("m-1")


@pytest.mark.parametrize(
    ("response", "absent"),
    [
        (httpx.Response(404, json={}), True),
        (httpx.Response(200, json={"state": "destroyed"}), True),
        (httpx.Response(200, json={"state": "started"}), False),
        (httpx.Response(200, json={"state": "stopped"}), False),
    ],
)
async def test_fly_absence(monkeypatch, response, absent):
    provider, requests = _fly(monkeypatch, lambda _request: response)
    assert await provider.resource_absent("m-1", pool="legacy_scope") is absent
    assert requests[0][:2] == ("GET", "/v1/apps/scope-app/machines/m-1")


async def test_fly_legacy_pool_without_credentials_fails_closed(monkeypatch):
    provider, requests = _fly(monkeypatch, lambda _request: httpx.Response(200, json={}))
    monkeypatch.setattr(settings, "SCOPE_SANDBOX_FLY_TOKEN", "")
    with pytest.raises(ValueError):
        await provider.destroy_resource("m-1", pool="legacy_scope")
    monkeypatch.setattr(settings, "SCOPE_SANDBOX_FLY_TOKEN", "scope-token")
    monkeypatch.setattr(settings, "SCOPE_SANDBOX_FLY_APP", "")
    with pytest.raises(ValueError):
        await provider.resource_absent("m-1", pool="legacy_scope")
    assert requests == []
