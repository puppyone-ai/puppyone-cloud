"""Shared computer semantics exercised against actual sandbox processes/files."""

import asyncio
from uuid import uuid4

import pytest

from src.platform.scope_sandbox.execution.pi_worker import PiWorker
from src.platform.scope_sandbox.execution.store import InMemoryExecutionSessionStore
from tests.agent.runtime.test_pi_worker import completion, config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def task(workbench, generation, uid):
    worker = PiWorker(str(uuid4()), "shared-fixture", provider="docker",
                      store=InMemoryExecutionSessionStore())
    worker.bind_workbench(workbench, generation, uid)
    return worker


async def next_model(worker):
    async with asyncio.timeout(30):
        while True:
            frame = await worker.receive()
            if frame["type"] == "model_request":
                return frame
            if frame["type"] == "checkpoint":
                await worker.send({"type": "reply", "id": frame["id"]})
            elif frame["type"] in {"failed", "disconnected"}:
                pytest.fail(str(frame))


async def file_tool(worker, content):
    frame = await next_model(worker)
    await completion(worker, frame, call=("write", {"path": "shared.txt", "content": content}))
    async with asyncio.timeout(30):
        while True:
            frame = await worker.receive()
            if frame["type"] in {"checkpoint", "tool_start", "tool_end"}:
                if frame["type"] == "tool_end":
                    assert not frame["result"].get("isError"), frame
                await worker.send({"type": "reply", "id": frame["id"], "allow": True})
                if frame["type"] == "tool_end":
                    return
            elif frame["type"] in {"failed", "disconnected"}:
                pytest.fail(str(frame))


@pytest.mark.parametrize("action", ["stop", "finalize", "park"])
async def test_finishing_one_task_preserves_peer_terminal_and_shared_files(action):
    workbench, generation = str(uuid4()), str(uuid4())
    first, second = task(workbench, generation, 20001), task(workbench, generation, 20002)
    resource = None
    try:
        resource = await first.create()
        await first.start(config())
        await file_tool(first, "first task")
        await second.join(resource)
        # Joining a running computer must not reset/fetch over its dirty files.
        await second.start(config())
        await file_tool(second, "second task")
        container = resource["resource_id"]
        await first._docker("exec", "-d", "--user=1000:1000", container,
                            "sh", "-c", "echo $$ > /workspace/terminal.pid; exec sleep 600")
        await first.control(action, {"message": "shared computer"})
        assert await second._docker("exec", container, "cat", "/workspace/repo/shared.txt") == "second task"
        await second._docker("exec", container, "sh", "-c", "kill -0 $(cat /workspace/terminal.pid)")
        # The other Pi process remains able to receive a model response.
        await completion(second, await next_model(second), text="Still running")
        async with asyncio.timeout(20):
            while True:
                frame = await second.receive()
                if frame["type"] == "checkpoint":
                    await second.send({"type": "reply", "id": frame["id"]})
                elif frame["type"] == "finished":
                    break
                elif frame["type"] in {"failed", "disconnected"}:
                    pytest.fail(str(frame))
    finally:
        for worker in (first, second):
            await worker.detach()
        if resource:
            await PiWorker.cleanup(resource, store=first.store)


async def test_task_cannot_snapshot_or_restore_the_shared_computer():
    worker = task(str(uuid4()), str(uuid4()), 20001)
    resource = None
    try:
        resource = await worker.create()
        await worker.start(config())
        await file_tool(worker, "must survive recovery")
        with pytest.raises(ValueError, match="workbench"):
            await worker.snapshot()
        with pytest.raises(RuntimeError, match="workbench"):
            await worker.control("snapshot")
        with pytest.raises(RuntimeError, match="workbench"):
            await worker.control("prepare", {**config()["workspace"], "project_id": "shared-fixture",
                                             "restore": str(uuid4())})
        assert await worker._docker("exec", resource["resource_id"], "cat", "/workspace/repo/shared.txt") == "must survive recovery"
    finally:
        await worker.detach()
        if resource:
            await PiWorker.cleanup(resource, store=worker.store)
