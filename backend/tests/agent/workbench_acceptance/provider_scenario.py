"""One product acceptance scenario for each real provider; only the model is scripted."""

import asyncio
import base64
import hashlib
import json
import os
import shlex
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn

from src.config import settings
from src.platform.access.adapters.agent.runtime import worker as dispatcher
from src.platform.access.adapters.agent.runtime.control.models import TERMINAL
from src.platform.authorization.factory import build_authorization_service
from src.platform.scope_sandbox.workbench_service import build_workbench_service
from tests.agent.runtime.test_supervisor import BashModel
from tests.agent.workbench_acceptance.accept_production_entry import request


class Model:
    def __init__(self):
        self.ready, self.release = asyncio.Event(), asyncio.Event()
        self.delegate = None

    async def completion(self, *args):
        self.ready.set()
        await self.release.wait()
        return await self.delegate.completion(*args)


@asynccontextmanager
async def http_server(app, headers):
    """Real HTTP is required to test streaming responses before they terminate."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(15):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.05)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
                headers=headers,
                timeout=200,
                trust_env=False,
            ) as http:
                yield http
        finally:
            if service := getattr(app.state, "workbench_service", None):
                await service.close()
            server.should_exit = True
            try:
                async with asyncio.timeout(15):
                    await task
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def eventually(action, predicate, *, timeout=180):
    async with asyncio.timeout(timeout):
        while True:
            value = await action()
            if predicate(value):
                return value
            await asyncio.sleep(0.3)


async def accept_runtime(case, monkeypatch, probe):
    provider, model = probe.provider, Model()
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROVIDER", provider.name)
    receipt = {
        "provider": provider.name,
        "result": "failed",
        "project_id": case.project,
        "artifact": provider.artifact(None),
        "stages": [],
        "metering": "disabled (not billing acceptance)",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty_source": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "source_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "HEAD", "--binary"])
        ).hexdigest(),
    }
    monkeypatch.setattr(dispatcher, "get_inference_service", lambda *_: model)
    worker_task, watcher = None, None
    start = time.monotonic()

    def passed(stage):
        receipt["stages"].append(stage)
        receipt.setdefault("stage_seconds", []).append(round(time.monotonic() - start, 2))
        print("PROVIDER_RUNTIME_STAGE " + stage, flush=True)

    try:
        async with http_server(case.app, case.http.headers) as http:
            root = f"/api/v1/projects/{case.project}/workbench"
            denied = await http.get(root, headers={"Authorization": ""})
            assert denied.status_code == 401
            assert (await http.get(root)).json() == {"workspace": None}
            model.delegate = BashModel(
                "set -eu; python3 -c " + shlex.quote(probe.egress_probe) + "; "
                'test "$(cat seed.txt)" = seed; '
                'umask 077; printf "saved before Git" > atomic.tmp; mv atomic.tmp agent.txt; '
                "for i in $(seq 1 1200); do test -f release.txt && break; sleep 0.1; done; "
                'test "$(cat human.txt)" = "edited through API"; test -f release.txt'
            )
            first = await http.post("/api/v1/agents/runs", json=request(case))
            assert first.status_code == 202, first.text
            run = first.json()
            receipt["run_ids"] = [run["id"]]
            worker_task = asyncio.create_task(dispatcher.serve())  # Default owner and ports.

            async def model_ready():
                if worker_task.done():
                    await worker_task
                current = (await http.get(f"/api/v1/agents/runs/{run['id']}")).json()
                assert current["state"] not in TERMINAL, current
                return model.ready.is_set()

            await eventually(model_ready, bool, timeout=200)
            status = (await http.get(root)).json()["workspace"]
            assert status["state"] == "running"
            generation, workbench = status["generation"], status["id"]
            assert run["workbench_id"] == workbench
            row = (
                case.client.table("cloud_workbenches")
                .select("*")
                .eq("id", workbench)
                .execute()
                .data[0]
            )
            resource = row["resource"]
            receipt.update(
                workbench_id=workbench,
                generation=generation,
                resource_id=resource["resource_id"],
                ready_seconds=round(time.monotonic() - start, 2),
            )
            passed(f"authenticated Chat -> default dispatcher -> real {provider.name}/Pi")
            changes = []

            async def watch():
                async with http.stream(
                    "GET", root + "/events", params={"generation": generation}
                ) as response:
                    assert response.status_code == 200
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            changes.append(json.loads(line[6:]))

            watcher = asyncio.create_task(watch())

            async def observed():
                if watcher.done():
                    await watcher
                return len(changes)

            await eventually(observed, lambda count: count > 0, timeout=40)
            file_channels = case.app.state.workbench_service.channels
            shared_channel = next(iter(file_channels.entries.values())).channel
            assert shared_channel.service_receipt["workbench"] == workbench
            model.release.set()

            async def read(path):
                return await http.get(
                    root + "/file", params={"generation": generation, "path": path}
                )

            async def saved_file():
                current = (await http.get(f"/api/v1/agents/runs/{run['id']}")).json()
                assert current["state"] not in TERMINAL, current
                return await read("agent.txt")

            saved = await eventually(saved_file, lambda r: r.status_code == 200, timeout=90)
            assert base64.b64decode(saved.json()["content_base64"]) == b"saved before Git"
            await probe.verify_egress(resource, receipt)
            listing = await http.get(root + "/files", params={"generation": generation})
            assert listing.status_code == 200, listing.text
            assert "agent.txt" in {item["name"] for item in listing.json()["entries"]}
            live = (await http.get(f"/api/v1/agents/runs/{run['id']}")).json()
            assert live["state"] not in TERMINAL and not live["publication"]
            # The private branch has no publication while native FS already has the bytes.
            assert (
                case.postgres.sql(
                    "SELECT count(*) FROM version_repository_refs "
                    f"WHERE project_id='{case.project}' AND name=decode('{status['target_ref'].encode().hex()}','hex')"
                )
                == "0"
            )
            await eventually(observed, lambda count: count > 1, timeout=30)
            assert all(change.get("generation") == generation for change in changes)
            assert len(file_channels.entries) == 1
            assert next(iter(file_channels.entries.values())).channel is shared_channel
            passed("native file reads and SSE watch share one admitted service connection")

            async def write(path, value, revision=None):
                return await http.put(
                    root + "/file",
                    params={"generation": generation},
                    json={
                        "path": path,
                        "content_base64": base64.b64encode(value).decode(),
                        "expected_revision": revision,
                    },
                )

            edited = await write("human.txt", b"edited through API")
            assert edited.status_code == 200, edited.text
            conflict = await write("human.txt", b"stale write", "0" * 64)
            assert conflict.status_code == 409, conflict.text
            assert (await write("release.txt", b"continue")).status_code == 200
            passed(
                "task IPv4/IPv6 egress denied -> Agent atomic save -> live read/watch before Git -> editor write -> Bash sees edit"
            )

            async def completed(identity):
                async def snapshot():
                    response = await http.get(f"/api/v1/agents/runs/{identity}")
                    assert response.status_code == 200, response.text
                    return response.json()

                final = await eventually(snapshot, lambda r: r["state"] in TERMINAL)
                assert final["state"] == "succeeded", final
                active = (
                    case.client.table("cloud_workbench_tasks")
                    .select("execution_id")
                    .eq("run_id", identity)
                    .eq("state", "active")
                    .execute()
                    .data
                )
                assert not active, "Terminal Run retained an unconfirmed task cleanup"
                return final

            final = await completed(run["id"])
            assert final["publication"]["target_ref"] == status["target_ref"]
            grant = build_authorization_service().resolve_project_grant(case.project, case.user)
            ops = case.engine.product_operations()
            with ops.open_read(
                case.project, grant, selector=status["target_ref"].encode()
            ) as reader:
                assert reader.read_file(case.project, "agent.txt") == b"saved before Git"
                assert reader.read_file(case.project, "human.txt") == b"edited through API"
            passed("real stock Git publication -> native refs and S3 objects")
            receipt["publication"] = final["publication"]

            model.delegate = BashModel(
                "set -eu; test -f agent.txt; test -f human.txt; printf second > second.txt"
            )
            second = await http.post("/api/v1/agents/runs", json=request(case))
            assert second.status_code == 202, second.text
            other = second.json()
            assert other["session_id"] != run["session_id"] and other["workbench_id"] == workbench
            receipt["run_ids"].append(other["id"])
            await completed(other["id"])
            await probe.assert_single_resource(resource, case.project)
            passed("independent Chat sessions reuse one resource and directory")

            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            owner = build_workbench_service(engine=case.engine)
            current = owner.repository.get(workbench)
            claimed = owner.repository.lifecycle_claim(current, suspend=True)
            assert claimed["acquired"]
            await owner._pause(claimed["workbench"])
            await probe.assert_paused(resource)
            assert (await read("agent.txt")).status_code == 409  # Reading cannot wake compute.
            await probe.assert_paused(resource)
            reopened = await http.post(root)
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()["generation"] == generation
            assert (await read("agent.txt")).json()["content_base64"] == saved.json()[
                "content_base64"
            ]
            assert (
                base64.b64decode((await read("second.txt")).json()["content_base64"]) == b"second"
            )
            assert (
                owner.repository.get(workbench)["resource"]["resource_id"]
                == resource["resource_id"]
            )
            passed(
                "lifecycle pause -> reads do not wake -> explicit reopen preserves resource/files"
            )
            receipt["result"] = "runtime_passed_cleanup_pending"
    finally:
        for task in (watcher, worker_task):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (watcher, worker_task) if t), return_exceptions=True)
        try:
            await probe.cleanup(case, receipt)
            if receipt.get("result") == "runtime_passed_cleanup_pending":
                receipt["result"] = "passed"
        finally:
            await provider.aclose()
            receipt["elapsed_seconds"] = round(time.monotonic() - start, 2)
            destination = os.environ.get(f"CLOUD_AGENT_{provider.name.upper()}_TEST_RECEIPT")
            if destination:
                Path(destination).write_text(json.dumps(receipt, indent=2) + "\n")
            print("PROVIDER_RUNTIME_RECEIPT " + json.dumps(receipt))
