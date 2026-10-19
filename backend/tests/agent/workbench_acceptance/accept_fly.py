"""Opt-in real Fly chain. Never substitutes provider, Pi, dispatcher or Git.

Run explicitly with CLOUD_AGENT_TEST_FLY=1 and CLOUD_AGENT_FLY_{APP,TOKEN,IMAGE,CLI}.
Only model responses are deterministic. Metering is disabled by the owned
infrastructure fixture: this proves runtime integration, not billing acceptance.
"""

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
from uuid import UUID

import httpx
import pytest
import uvicorn

from src.config import settings
from src.platform.access.adapters.agent.runtime import worker as dispatcher
from src.platform.access.adapters.agent.runtime.control.models import TERMINAL
from src.platform.authorization.factory import build_authorization_service
from src.platform.scope_sandbox.execution.providers.fly import FlyRuntimeProvider
from src.platform.scope_sandbox.workbench_service import build_workbench_service
from tests.agent.runtime.test_supervisor import BashModel
from tests.agent.workbench_acceptance.accept_production_entry import (
    control_api as control_api,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    entry as entry,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    object_endpoint as object_endpoint,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    postgres as postgres,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    request,
)
from tests.agent.workbench_acceptance.accept_production_entry import (
    services as services,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("CLOUD_AGENT_TEST_FLY") != "1", reason="explicit Fly opt-in required"
    ),
]


@pytest.fixture(scope="session", autouse=True)
def fly_configuration():
    # Fail before opening owned infrastructure or allocating a billable resource.
    missing = [
        name
        for name in ("CLOUD_AGENT_FLY_APP", "CLOUD_AGENT_FLY_TOKEN", "CLOUD_AGENT_FLY_IMAGE")
        if not getattr(settings, name)
    ]
    if missing:
        pytest.fail("Live Fly test requires: " + ", ".join(missing))
    FlyRuntimeProvider().validate(settings.CLOUD_AGENT_FLY_IMAGE)


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


async def firewall_hits(resource):
    """Read the provider firewall counter; product files still use their API."""
    process = await asyncio.create_subprocess_exec(
        settings.CLOUD_AGENT_FLY_CLI,
        "ssh",
        "console",
        "--quiet",
        "--app",
        resource["app"],
        "--machine",
        resource["resource_id"],
        "--user",
        "root",
        "--command",
        "nft -j list chain inet puppyone_task_egress output",
        env={**os.environ, "FLY_API_TOKEN": settings.CLOUD_AGENT_FLY_TOKEN},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(45):
            stdout, _stderr = await process.communicate()
        assert process.returncode == 0, "Unable to read Fly firewall counter"
        return sum(
            expression["counter"]["packets"]
            for item in json.loads(stdout)["nftables"]
            for expression in item.get("rule", {}).get("expr", [])
            if "counter" in expression
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def cleanup_owned(case, provider, receipt):
    """Delete only resources identified by this new test Project/generations.

    Cleanup also resolves lost create replies and an unattached disk created
    before Machine allocation failed. Production retains disks; this explicit
    test teardown owns and discards its disposable files.
    """
    rows = (
        case.client.table("cloud_workbenches")
        .select("*")
        .eq("project_id", case.project)
        .execute()
        .data
    )
    for row in rows:
        resource = row["resource"]
        if not resource:
            continue
        matches = await provider.resolve(resource)
        for resolved in matches:
            await provider.cleanup(resolved)
        await eventually(
            lambda resource=resource: provider._request(resource, "GET", "/machines"),
            lambda machines: (
                not any(
                    m.get("config", {}).get("metadata", {}).get("project_id") == case.project
                    for m in machines
                )
            ),
            timeout=60,
        )
        name = "pw_" + base64.b32encode(UUID(row["generation"]).bytes).decode().rstrip("=").lower()
        volumes = await eventually(
            lambda resource=resource: provider._request(resource, "GET", "/volumes"),
            lambda volumes, name=name: all(
                v["name"] != name or not v.get("attached_machine_id") for v in volumes
            ),
            timeout=60,
        )
        for volume in volumes:
            if volume["name"] != name:
                continue
            assert not volume.get("attached_machine_id"), "Owned disk still attached; retaining"
            await provider._request(resource, "DELETE", "/volumes/" + volume["id"])
        deleted = await eventually(
            lambda resource=resource: provider._request(resource, "GET", "/volumes"),
            # Fly soft-deletes Volumes; pending_destroy stays in the raw API
            # list but is no longer attachable or billed (Fly billing docs).
            lambda volumes, name=name: all(
                v["name"] != name
                or (v["state"] == "pending_destroy" and not v.get("attached_machine_id"))
                for v in volumes
            ),
            timeout=60,
        )
        receipt.setdefault("deleted_volumes", []).extend(
            {"id": v["id"], "state": v["state"]} for v in deleted if v["name"] == name
        )
    receipt["cleanup"] = "owned Machines absent; Volumes deleted (absent or pending_destroy)"


async def test_fly_chat_live_files_publication_and_resume(entry, monkeypatch):
    case, provider, model = entry, FlyRuntimeProvider(), Model()
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROVIDER", "fly")
    receipt = {
        "provider": "fly",
        "region": settings.CLOUD_AGENT_FLY_REGION,
        "result": "failed",
        "project_id": case.project,
        "image": settings.CLOUD_AGENT_FLY_IMAGE,
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
        print("FLY_RUNTIME_STAGE " + stage, flush=True)

    try:
        async with http_server(case.app, case.http.headers) as http:
            root = f"/api/v1/projects/{case.project}/workbench"
            denied = await http.get(root, headers={"Authorization": ""})
            assert denied.status_code == 401
            assert (await http.get(root)).json() == {"workspace": None}
            egress_probe = """
import errno, os, socket
assert os.getuid() == 1000
for family, host in [(socket.AF_INET, '1.1.1.1'), (socket.AF_INET6, '2606:4700:4700::1111')]:
    with socket.socket(family, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        try:
            connection.connect((host, 443))
        except OSError as error:
            # ICMP admin-prohibited maps to EHOSTUNREACH for IPv4 on Fly.
            assert error.errno in (errno.EACCES, errno.EPERM, errno.EHOSTUNREACH), repr(error)
        else:
            raise AssertionError('Unrestricted task egress: ' + host)
"""
            model.delegate = BashModel(
                "python3 -c " + shlex.quote(egress_probe) + "; "
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
                machine_id=resource["resource_id"],
                volume_id=resource["volume_id"],
                ready_seconds=round(time.monotonic() - start, 2),
            )
            passed("authenticated Chat -> default dispatcher -> real Fly/Pi")
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
            receipt["firewall_rejected_packets"] = await firewall_hits(resource)
            assert receipt["firewall_rejected_packets"] >= 2
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
                "test -f agent.txt; test -f human.txt; printf second > second.txt"
            )
            second = await http.post("/api/v1/agents/runs", json=request(case))
            assert second.status_code == 202, second.text
            other = second.json()
            assert other["session_id"] != run["session_id"] and other["workbench_id"] == workbench
            receipt["run_ids"].append(other["id"])
            await completed(other["id"])
            machines = await provider._request(resource, "GET", "/machines")
            owned = [
                m
                for m in machines
                if m.get("config", {}).get("metadata", {}).get("project_id") == case.project
            ]
            assert [m["id"] for m in owned] == [resource["resource_id"]]
            passed("independent Chat sessions reuse one Machine and directory")

            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            owner = build_workbench_service(engine=case.engine)
            current = owner.repository.get(workbench)
            claimed = owner.repository.lifecycle_claim(current, suspend=True)
            assert claimed["acquired"]
            await owner._pause(claimed["workbench"])
            assert (await provider.inspect(resource))["state"] == "stopped"
            assert (await read("agent.txt")).status_code == 409  # Reading cannot wake compute.
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
                "lifecycle stop -> reads do not wake -> explicit reopen preserves Machine/disk/files"
            )
            receipt["result"] = "runtime_passed_cleanup_pending"
    finally:
        for task in (watcher, worker_task):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (watcher, worker_task) if t), return_exceptions=True)
        try:
            await cleanup_owned(case, provider, receipt)
            if receipt.get("result") == "runtime_passed_cleanup_pending":
                receipt["result"] = "passed"
        finally:
            await provider.aclose()
            receipt["elapsed_seconds"] = round(time.monotonic() - start, 2)
            destination = os.environ.get("CLOUD_AGENT_FLY_TEST_RECEIPT")
            if destination:
                Path(destination).write_text(json.dumps(receipt, indent=2) + "\n")
            print("FLY_RUNTIME_RECEIPT " + json.dumps(receipt))
