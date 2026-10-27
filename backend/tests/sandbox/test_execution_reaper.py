"""The sandbox reaper stops idle unretained ephemeral resources through providers."""

import asyncio

import pytest

from src.platform.sandbox import reaper
from src.platform.sandbox.store import ExecutionSession, InMemoryExecutionSessionStore

NOW = 10_000.0


def _session(session_id, provider, *, idle_s, resource_id="resource-" + "x"):
    return ExecutionSession(
        session_id=session_id,
        provider=provider,
        resource_id=resource_id,
        readonly=False,
        created_at=NOW - idle_s,
        last_activity=NOW - idle_s,
        project_id="project-1",
    )


class _Provider:
    def __init__(self, calls, *, fail=False):
        self.calls, self.fail = calls, fail

    async def cleanup(self, resource):
        self.calls.append(("cleanup", resource))
        if self.fail:
            raise RuntimeError("provider unavailable")

    async def aclose(self):
        self.calls.append(("aclose", None))


def _use_provider(monkeypatch, calls, *, fail=False):
    names = []

    def factory(name):
        names.append(name)
        return _Provider(calls, fail=fail)

    monkeypatch.setattr(reaper, "runtime_provider", factory)
    return names


async def test_idle_unretained_resources_are_cleaned_through_their_provider(monkeypatch):
    store = InMemoryExecutionSessionStore()
    store.put(_session("e2b-idle", "e2b", idle_s=1800, resource_id="sbx-1"))
    store.put(_session("docker-idle", "docker", idle_s=600, resource_id="ctr-1"))
    store.put(_session("e2b-active", "e2b", idle_s=60))
    store.put(_session("fly-idle", "fly", idle_s=86_400))
    calls = []
    names = _use_provider(monkeypatch, calls)

    stopped = await reaper.run_execution_reaper_once(store, now=NOW)

    assert stopped == 2
    assert sorted(names) == ["docker", "e2b"]
    cleaned = sorted(
        (resource for kind, resource in calls if kind == "cleanup"),
        key=lambda resource: resource["session_id"],
    )
    assert cleaned == [
        {"provider": "docker", "resource_id": "ctr-1", "session_id": "docker-idle",
         "project_id": "project-1"},
        {"provider": "e2b", "resource_id": "sbx-1", "session_id": "e2b-idle",
         "project_id": "project-1"},
    ]
    assert [kind for kind, _ in calls].count("aclose") == 2
    assert store.get("e2b-idle") is None and store.get("docker-idle") is None
    # Active sessions and providers without a bounded lifetime are untouched.
    assert store.get("e2b-active") is not None
    assert store.get("fly-idle") is not None


async def test_claimed_session_without_resource_is_forgotten_without_provider_call(monkeypatch):
    store = InMemoryExecutionSessionStore()
    store.put(_session("claimed", "e2b", idle_s=1800, resource_id=""))
    calls = []
    _use_provider(monkeypatch, calls)

    assert await reaper.run_execution_reaper_once(store, now=NOW) == 1
    assert calls == []
    assert store.get("claimed") is None


async def test_failed_cleanup_keeps_the_session_for_the_next_pass(monkeypatch):
    store = InMemoryExecutionSessionStore()
    store.put(_session("e2b-idle", "e2b", idle_s=1800))
    calls = []
    _use_provider(monkeypatch, calls, fail=True)

    assert await reaper.run_execution_reaper_once(store, now=NOW) == 0
    assert store.get("e2b-idle") is not None
    assert ("aclose", None) in calls

    _use_provider(monkeypatch, [])
    assert await reaper.run_execution_reaper_once(store, now=NOW) == 1
    assert store.get("e2b-idle") is None


async def test_real_e2b_provider_kills_the_recorded_sandbox(monkeypatch):
    from e2b import AsyncSandbox

    killed = []

    async def kill(resource_id, **_options):
        killed.append(resource_id)
        return True

    monkeypatch.setattr(AsyncSandbox, "kill", kill)
    store = InMemoryExecutionSessionStore()
    store.put(_session("e2b-idle", "e2b", idle_s=1800, resource_id="sbx-real"))

    assert await reaper.run_execution_reaper_once(store, now=NOW) == 1
    assert killed == ["sbx-real"]
    assert store.get("e2b-idle") is None


async def test_real_docker_provider_removes_the_recorded_container(monkeypatch):
    commands = []

    class _Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def create_subprocess_exec(*args, **_kwargs):
        commands.append(args)
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    store = InMemoryExecutionSessionStore()
    store.put(_session("docker-idle", "docker", idle_s=600, resource_id="ctr-real"))

    assert await reaper.run_execution_reaper_once(store, now=NOW) == 1
    assert commands == [("docker", "rm", "-f", "ctr-real")]
    assert store.get("docker-idle") is None


@pytest.mark.parametrize("missing", [b"No such container: ctr-gone"])
async def test_docker_container_already_gone_still_forgets_the_session(monkeypatch, missing):
    class _Process:
        returncode = 1

        async def communicate(self):
            return b"", missing

    async def create_subprocess_exec(*_args, **_kwargs):
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    store = InMemoryExecutionSessionStore()
    store.put(_session("docker-gone", "docker", idle_s=600, resource_id="ctr-gone"))

    assert await reaper.run_execution_reaper_once(store, now=NOW) == 1
    assert store.get("docker-gone") is None
