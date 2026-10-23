"""Shared private tunnels stay scoped by app, token and event loop."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.platform.workspace.execution.providers.fly_transport import (
    acquire_transport,
    close_transports,
)

pytestmark = pytest.mark.asyncio


async def test_reuses_one_owned_agent_and_isolates_credentials(monkeypatch):
    processes = []

    async def spawn(*args, **kwargs):
        assert Path(args[-1]).is_file()
        assert Path(args[-1]).stat().st_mode & 0o777 == 0o600
        Path(kwargs["env"]["FLY_AGENT_SOCKET_PATH"]).touch()
        process = SimpleNamespace(
            stdin=SimpleNamespace(close=Mock()), wait=AsyncMock(return_value=0), returncode=None
        )
        processes.append((process, args, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    try:
        first, second = await asyncio.gather(
            *(acquire_transport("app", "token", "fly") for _ in range(2))
        )
        assert first is second and first.users == 2 and len(processes) == 1
        first.release()
        assert first.users == 1 and first.idle_task is None
        third = await acquire_transport("other", "token", "fly")
        fourth = await acquire_transport("app", "rotated", "fly")
        assert len(processes) == 3
        assert len({item[2]["env"]["FLY_AGENT_SOCKET_PATH"] for item in processes}) == 3
        assert all(item[2]["env"]["FLY_AGENT_ISOLATED"] == "1" for item in processes)
        second.release()
        third.release()
        fourth.release()
        again = await acquire_transport("app", "token", "fly")
        assert again is first and len(processes) == 3
        again.release()
        directories = [Path(item[2]["env"]["FLY_CONFIG_DIR"]) for item in processes]
    finally:
        await close_transports()
    assert all(not directory.exists() for directory in directories)
    for process, _, _ in processes:
        process.stdin.close.assert_called_once()
        process.wait.assert_awaited_once()


async def test_cancelled_start_closes_owned_agent_and_can_retry(monkeypatch):
    started = asyncio.Event()
    processes = []

    async def spawn(*args, **kwargs):
        process = SimpleNamespace(
            stdin=SimpleNamespace(close=Mock()), wait=AsyncMock(return_value=0), returncode=None
        )
        processes.append(process)
        if len(processes) > 1:
            Path(kwargs["env"]["FLY_AGENT_SOCKET_PATH"]).touch()
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    try:
        task = asyncio.create_task(acquire_transport("app", "token", "fly"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        processes[0].stdin.close.assert_called_once()
        processes[0].wait.assert_awaited_once()
        transport = await acquire_transport("app", "token", "fly")
        assert transport.users == 1 and len(processes) == 2
        transport.release()
    finally:
        await close_transports()
