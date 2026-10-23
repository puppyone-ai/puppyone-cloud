"""The supervisor owns networking; reloadable children only borrow it."""

import asyncio
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import local_fly_agent
from src.platform.workspace.execution.providers.fly_agent import (
    MANAGED_AGENT,
    managed_environment,
)
from src.platform.workspace.execution.providers.fly_transport import (
    acquire_transport,
    close_transports,
)


@pytest.fixture
def owned(monkeypatch):
    sockets, processes = [], []

    def start(command, **kwargs):
        channel = socket.socket(socket.AF_UNIX)
        channel.bind(kwargs["env"]["FLY_AGENT_SOCKET_PATH"])
        sockets.append(channel)
        process = SimpleNamespace(
            poll=Mock(return_value=None),
            stdin=SimpleNamespace(close=Mock()),
            wait=Mock(),
            kill=Mock(),
        )
        processes.append(process)
        assert "FLY_API_TOKEN" in kwargs["env"]
        assert all("fixture-token" not in arg for arg in command)
        return process

    monkeypatch.setattr(local_fly_agent.subprocess, "Popen", start)
    owner = local_fly_agent.LocalFlyAgent()
    environment = {
        "CLOUD_AGENT_SANDBOX_PROVIDER": "fly",
        "CLOUD_AGENT_FLY_APP": "app",
        "CLOUD_AGENT_FLY_TOKEN": "fixture-token",
        "CLOUD_AGENT_FLY_CLI": "fly",
    }
    owner.configure(environment)
    monkeypatch.setenv(MANAGED_AGENT, environment[MANAGED_AGENT])
    try:
        yield owner, environment, processes
    finally:
        owner.close()
        for channel in sockets:
            channel.close()


@pytest.mark.asyncio
async def test_api_reload_does_not_restart_or_close_supervisor_agent(owned, monkeypatch):
    owner, environment, processes = owned
    spawn = Mock(side_effect=AssertionError("child must not spawn an agent"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    try:
        first = await acquire_transport("app", "fixture-token", "fly")
        assert first.managed
        first.release()
        await close_transports()  # API shutdown; supervisor is still alive.
        owner.configure(environment)  # Source reload under unchanged configuration.
        second = await acquire_transport("app", "fixture-token", "fly")
        assert second is not first and second.env == first.env
        second.release()
        assert len(processes) == 1
        processes[0].stdin.close.assert_not_called()
    finally:
        await close_transports()


@pytest.mark.parametrize(
    "change", ["token", "app", "cli", "permissions", "dead_owner", "socket_symlink", "malformed"]
)
def test_changed_or_untrusted_owner_fails_closed(owned, change):
    _, environment, _ = owned
    root = Path(environment[MANAGED_AGENT])
    args = {"app": "app", "token": "fixture-token", "cli": "fly"}
    if change in args:
        args[change] = "changed"
    elif change == "permissions":
        (root / "owner.json").chmod(0o644)
    elif change == "dead_owner":
        descriptor = root / "owner.json"
        value = json.loads(descriptor.read_text())
        value["pid"] = 2_147_483_647
        descriptor.write_text(json.dumps(value))
    elif change == "socket_symlink":
        (root / "agent.sock").rename(root / "actual.sock")
        (root / "agent.sock").symlink_to(root / "actual.sock")
    elif change == "malformed":
        (root / "owner.json").write_text("[]")
    with pytest.raises(ConnectionError, match="unavailable or has changed"):
        managed_environment(**args)


def test_rotation_replaces_only_owned_agent_and_never_persists_token(owned):
    owner, environment, processes = owned
    old_root = Path(environment[MANAGED_AGENT])
    environment["CLOUD_AGENT_FLY_TOKEN"] = "rotated-token"
    owner.configure(environment)
    assert len(processes) == 2 and not old_root.exists()
    processes[0].stdin.close.assert_called_once()
    descriptor = Path(environment[MANAGED_AGENT]) / "owner.json"
    assert "rotated-token" not in descriptor.read_text()
    assert "fixture-token" not in descriptor.read_text()
    assert json.loads(descriptor.read_text())["pid"] == os.getpid()
    root = descriptor.parent
    owner.close()
    assert not root.exists()
    processes[1].stdin.close.assert_called_once()


def test_dead_agent_is_replaced_on_next_child_launch(owned):
    owner, environment, processes = owned
    processes[0].poll.return_value = 1
    assert not owner.healthy()
    owner.configure(environment)
    assert len(processes) == 2
    assert owner.healthy()


def test_non_fly_configuration_does_not_adopt_inherited_agent(monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(local_fly_agent.subprocess, "Popen", spawn)
    owner = local_fly_agent.LocalFlyAgent()
    environment = {MANAGED_AGENT: "/other/owner", "CLOUD_AGENT_SANDBOX_PROVIDER": "docker"}
    owner.configure(environment)
    assert MANAGED_AGENT not in environment
    spawn.assert_not_called()
