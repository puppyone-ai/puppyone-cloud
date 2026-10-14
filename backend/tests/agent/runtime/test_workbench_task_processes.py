"""Real Pi processes, detached children, a real PTY and HTTP preview in one VM.

The model speaks the deterministic fixture protocol; no inference/provider keys
are used. Every container/volume is owned by this test and bounded by timeouts.
"""

import asyncio
import json
from contextlib import suppress
from uuid import uuid4

import pytest

from src.platform.scope_sandbox.execution.pi_worker import PiWorker
from src.platform.scope_sandbox.execution.store import InMemoryExecutionSessionStore
from tests.agent.runtime.test_pi_worker import completion, config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
IMAGE = "puppyone-cloud-agent:issue-118-process-tests"


async def until_file(owner, path, expected):
    async with asyncio.timeout(30):
        while True:
            content = await owner._docker(
                "exec",
                "--user=1001:1000",
                owner.resource["resource_id"],
                "sh",
                "-c",
                'cat "$1" 2>/dev/null || true',
                "sh",
                path,
            )
            if content and expected in content:
                return content
            await asyncio.sleep(0.05)


async def drive(worker, command, admitted):
    async with asyncio.timeout(60):
        first = True
        while True:
            frame = await worker.receive()
            kind = frame["type"]
            if kind == "model_request":
                if first:
                    first = False
                    await completion(worker, frame, call=("bash", {"command": command}))
                else:
                    await completion(worker, frame, text="Done")
            elif kind == "tool_start":
                await worker.send({"type": "reply", "id": frame["id"], "allow": True})
                admitted.set()
            elif kind in {"tool_end", "checkpoint"}:
                await worker.send({"type": "reply", "id": frame["id"]})
            elif kind == "finished":
                assert not frame.get("error"), frame
                return
            elif kind in {"failed", "disconnected"}:
                raise AssertionError(frame)


async def read_marker(stream, marker):
    async with asyncio.timeout(15):
        while marker not in await stream.readline():
            if stream.at_eof():
                raise AssertionError("PTY closed before expected output")


@pytest.mark.parametrize("end_action", ["stop", "finalize", "disconnect"])
async def test_ending_one_real_agent_preserves_other_agent_pty_preview_and_files(end_action):
    from src.platform.scope_sandbox.execution.workbench_task import WorkbenchTask

    owner = PiWorker(
        str(uuid4()),
        "fixture",
        provider="docker",
        image=IMAGE,
        store=InMemoryExecutionSessionStore(),
    )
    workers, drivers, children = [], [], []
    workbench_id, generation = str(uuid4()), str(uuid4())
    try:
        owner.resource.update(workbench_id=workbench_id, workbench_generation=generation)
        resource = await owner.create()
        await owner._docker(
            "exec",
            resource["resource_id"],
            "node",
            "--input-type=module",
            "-e",
            "import {initializeWorkbench} from '/opt/puppyone-agent/workbench-initialize.mjs';"
            "process.umask(0o002); await initializeWorkbench("
            + json.dumps({"workbench": workbench_id, "generation": generation}) + ","
            + json.dumps({"project_id": "fixture", **config()["workspace"]})
            + ", () => {throw new Error('Empty fixture must not fetch')});",
        )
        for uid in (20000, 20001):
            task = WorkbenchTask(
                str(uuid4()),
                "fixture",
                workbench_id=workbench_id,
                generation=generation,
                task_uid=uid,
                provider="docker",
                image=IMAGE,
                store=InMemoryExecutionSessionStore(),
            )
            workers.append(task)
            await task.connect({**resource, "task_execution_id": task.task_execution_id, "task_uid": uid})
            await task.start(config())
        # Genuine terminal with a controlling PTY, not a substitute sleep process.
        terminal = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            "--workdir=/workspace/repo",
            "--user=1001:1000",
            resource["resource_id"],
            "script",
            "-qefc",
            "test \"$PWD\" = /workspace/repo && test -t 0 && printf 'TTY_READY\\n'; exec cat",
            "/dev/null",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        children.append(terminal)
        await read_marker(terminal.stdout, b"TTY_READY")
        await owner._docker(
            "exec",
            "-d",
            "--workdir=/workspace/repo",
            "--user=1001:1000",
            resource["resource_id"],
            "node",
            "-e",
            "require('http').createServer((q,s)=>s.end(require('fs').readFileSync('human-unsaved.txt'))).listen(8080,'127.0.0.1')",
        )
        admitted = [asyncio.Event(), asyncio.Event()]
        for i, task in enumerate(workers):
            # Both actual Pi bash tools stay live until externally released.
            # A detached descendant must be killed only with its owning task.
            command = (
                f"printf ready > agent-{i}.ready; "
                "while [ ! -e go ]; do sleep .05; done; "
                f"printf 'agent-{i}\\n' >> shared.txt; "
                f"setsid sh -c 'echo $$ > detached-{i}.pid; exec sleep 120' >/dev/null 2>&1 & "
                f"while :; do date +%s%N > agent-{i}.alive; sleep .1; done"
            )
            drivers.append(asyncio.create_task(drive(task, command, admitted[i])))
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in admitted)), 30)
        await until_file(owner, "/workspace/repo/agent-0.ready", "ready")
        await until_file(owner, "/workspace/repo/agent-1.ready", "ready")
        await owner._docker(
            "exec", "--user=1001:1000", resource["resource_id"], "touch", "/workspace/repo/go"
        )
        await until_file(owner, "/workspace/repo/shared.txt", "agent-0")
        await until_file(owner, "/workspace/repo/shared.txt", "agent-1")
        old = await until_file(owner, "/workspace/repo/agent-1.alive", "")
        await until_file(owner, "/workspace/repo/detached-0.pid", "")
        await until_file(owner, "/workspace/repo/detached-1.pid", "")
        # A current user's edit after both Agents started must survive task cleanup.
        await owner._docker(
            "exec",
            "--user=1001:1000",
            resource["resource_id"],
            "sh",
            "-c",
            "printf unique > /workspace/repo/human-unsaved.txt",
        )
        if end_action == "disconnect":
            workers[0].process.stdin.close()
            await asyncio.wait_for(workers[0].process.wait(), 15)
        else:
            await workers[0].control(end_action, {"message": "Shared worktree checkpoint"})
        async with asyncio.timeout(15):
            while await until_file(owner, "/workspace/repo/agent-1.alive", "") == old:
                await asyncio.sleep(0.05)
        terminal.stdin.write(b"PTY_AFTER_TASK_END\n")
        await terminal.stdin.drain()
        await read_marker(terminal.stdout, b"PTY_AFTER_TASK_END")
        preview = await owner._docker(
            "exec",
            "--user=1001:1000",
            resource["resource_id"],
            "node",
            "-e",
            "fetch('http://127.0.0.1:8080').then(r=>r.text()).then(console.log)",
        )
        assert preview == "unique"
        assert await until_file(owner, "/workspace/repo/human-unsaved.txt", "unique") == "unique"
        shared = await until_file(owner, "/workspace/repo/shared.txt", "agent-1")
        assert "agent-0" in shared
        # Detached owner children are gone; the other task's child is not killed.
        for index, alive in [(0, False), (1, True)]:
            state = await owner._docker(
                "exec",
                resource["resource_id"],
                "sh",
                "-c",
                f"pid=$(cat /workspace/repo/detached-{index}.pid); "
                'if [ -r /proc/$pid/status ]; then grep "^State:" /proc/$pid/status; fi',
            )
            assert (bool(state) and "Z" not in state) == alive
        with pytest.raises(PermissionError, match="workbench"):
            await workers[1].snapshot()
        with pytest.raises(PermissionError, match="workbench"):
            await workers[1].pause()
        assert json.loads(await owner._docker("inspect", resource["resource_id"]))[0]["State"][
            "Running"
        ]
    finally:
        for driver in drivers:
            driver.cancel()
        await asyncio.gather(*drivers, return_exceptions=True)
        for task in workers:
            with suppress(Exception):
                await task.stop()
        await owner.stop()
        for child in children:
            with suppress(Exception):
                await asyncio.wait_for(child.wait(), 10)
