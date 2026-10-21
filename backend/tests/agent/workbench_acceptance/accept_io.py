"""Same-workload, real provider I/O through the authorized product HTTP routes.

Opt in with CLOUD_AGENT_TEST_IO=fly or e2b (comma-separated for both).
Reports allocation, first connection, and warm I/O separately. Uses disposable
owned PostgreSQL/S3 and provider resources; no model or billing acceptance.
"""

import asyncio
import base64
import hashlib
import json
import math
import os
import subprocess
import time
from pathlib import Path

import pytest

from src.config import settings
from src.platform.scope_sandbox.execution.providers.registry import runtime_provider
from tests.agent.workbench_acceptance.accept_e2b import cleanup_owned as cleanup_e2b
from tests.agent.workbench_acceptance.accept_fly import cleanup_owned
from tests.agent.workbench_acceptance.accept_production_entry import (
    control_api as control_api,
)
from tests.agent.workbench_acceptance.accept_production_entry import entry as entry
from tests.agent.workbench_acceptance.accept_production_entry import (
    object_endpoint as object_endpoint,
)
from tests.agent.workbench_acceptance.accept_production_entry import postgres as postgres
from tests.agent.workbench_acceptance.accept_production_entry import services as services
from tests.agent.workbench_acceptance.provider_scenario import http_server

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def summary(values):
    ordered = sorted(values)
    return dict(
        count=len(values),
        p50_ms=round(ordered[math.ceil(len(values) * 0.5) - 1], 2),
        p95_ms=round(ordered[math.ceil(len(values) * 0.95) - 1], 2),
        max_ms=round(max(values), 2),
    )


@pytest.mark.parametrize("provider_name", os.environ.get("CLOUD_AGENT_TEST_IO", "").split(","))
async def test_real_provider_file_io(entry, monkeypatch, provider_name):
    if provider_name not in {"e2b", "fly"}:
        pytest.skip("explicit real-provider I/O opt-in required")
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROVIDER", provider_name)
    provider = runtime_provider(provider_name)
    receipt = dict(
        provider=provider_name,
        fly_region=settings.CLOUD_AGENT_FLY_REGION if provider_name == "fly" else None,
        result="failed",
        project_id=entry.project,
        source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        source_diff_sha256=hashlib.sha256(
            subprocess.check_output(["git", "diff", "HEAD", "--binary"])
        ).hexdigest(),
        workload="50 iterations of write/read/list; 4096-byte compressible ASCII text (counter plus dot padding); authenticated HTTP; local API+owned PG/S3",
        metering="disabled; not billing acceptance",
    )
    samples = {key: [] for key in ("write", "read", "list")}
    try:
        async with http_server(entry.app, entry.http.headers) as http:
            root = f"/api/v1/projects/{entry.project}/workbench"
            start = time.perf_counter()
            opened = await http.post(root)
            assert opened.status_code == 200, opened.text
            receipt["open_ms"] = round((time.perf_counter() - start) * 1000, 2)
            workspace = opened.json()
            params = {"generation": workspace["generation"]}
            start = time.perf_counter()
            first = await http.get(root + "/file", params={**params, "path": "seed.txt"})
            assert first.status_code == 200, first.text
            assert base64.b64decode(first.json()["content_base64"]) == b"seed"
            receipt["first_read_ms"] = round((time.perf_counter() - start) * 1000, 2)
            service = entry.app.state.workbench_service
            cached = next(iter(service.channels.entries.values())).channel
            receipt["write_encodings"] = cached.service_receipt.get("write_encodings", [])
            resource_before = service.repository.get(workspace["id"])["resource"]["resource_id"]
            start = time.perf_counter()
            reopened = await http.post(root)
            receipt["already_running_open_ms"] = round((time.perf_counter() - start) * 1000, 2)
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()["id"] == workspace["id"]
            assert reopened.json()["generation"] == workspace["generation"]
            assert (
                service.repository.get(workspace["id"])["resource"]["resource_id"]
                == resource_before
            )
            assert next(iter(service.channels.entries.values())).channel is cached
            revision = None
            for iteration in range(50):
                payload = (str(iteration).encode() + b"." * 4096)[:4096]
                start = time.perf_counter()
                wrote = await http.put(
                    root + "/file",
                    params=params,
                    json={
                        "path": "io.txt",
                        "content_base64": base64.b64encode(payload).decode(),
                        "expected_revision": revision,
                    },
                )
                samples["write"].append((time.perf_counter() - start) * 1000)
                assert wrote.status_code == 200, wrote.text
                revision = wrote.json()["revision"]
                start = time.perf_counter()
                read = await http.get(root + "/file", params={**params, "path": "io.txt"})
                samples["read"].append((time.perf_counter() - start) * 1000)
                assert read.status_code == 200, read.text
                assert base64.b64decode(read.json()["content_base64"]) == payload
                assert read.json()["revision"] == revision
                start = time.perf_counter()
                listing = await http.get(root + "/files", params=params)
                samples["list"].append((time.perf_counter() - start) * 1000)
                assert listing.status_code == 200, listing.text
                assert "io.txt" in {item["name"] for item in listing.json()["entries"]}
                assert next(iter(service.channels.entries.values())).channel is cached
            receipt["warm"] = {key: summary(values) for key, values in samples.items()}
            receipt["warm_channel_count"] = len(service.channels.entries)
            start = time.perf_counter()
            concurrent = await asyncio.gather(
                *(http.get(root + "/file", params={**params, "path": "io.txt"}) for _ in range(3))
            )
            receipt["three_concurrent_reads_ms"] = round((time.perf_counter() - start) * 1000, 2)
            assert all(response.status_code == 200 for response in concurrent)
            assert next(iter(service.channels.entries.values())).channel is cached
            # Repeated subscription ACK has the same transport but no Python
            # file helper, separating network/control overhead from filesystem I/O.
            controls = []
            for _ in range(10):
                start = time.perf_counter()
                await cached.control("watch")
                controls.append((time.perf_counter() - start) * 1000)
            receipt["control_roundtrip"] = summary(controls)
            await cached.control("unwatch")
            stale = await http.put(
                root + "/file",
                params=params,
                json={
                    "path": "io.txt",
                    "content_base64": "c3RhbGU=",
                    "expected_revision": "0" * 64,
                },
            )
            assert stale.status_code == 409
            denied = await http.get(
                root + "/file", params={**params, "path": "io.txt"}, headers={"Authorization": ""}
            )
            assert denied.status_code == 401
            # Closing SSH must not stop the VM-owned service or its native files.
            service_receipt = dict(cached.service_receipt)
            await service.channels.invalidate(workspace["id"])
            start = time.perf_counter()
            reconnected = await http.get(root + "/file", params={**params, "path": "io.txt"})
            receipt["transport_reconnect_read_ms"] = round((time.perf_counter() - start) * 1000, 2)
            assert reconnected.status_code == 200, reconnected.text
            assert base64.b64decode(reconnected.json()["content_base64"]) == payload
            replacement = next(iter(service.channels.entries.values())).channel
            assert replacement is not cached
            assert replacement.service_receipt == service_receipt
            receipt["service_survived_transport_disconnect"] = True
            row = service.repository.get(workspace["id"])
            receipt["resource_id"] = row["resource"]["resource_id"]
            receipt["artifact"] = row["binding"]["artifact"]
            claimed = service.repository.lifecycle_claim(row, suspend=True)
            assert claimed["acquired"]
            await service._pause(claimed["workbench"])
            paused = service.repository.get(workspace["id"])
            receipt["preservation"] = paused["resource"]["preservation"]
            assert (await http.get(root + "/files", params=params)).status_code == 409
            start = time.perf_counter()
            resumed = await http.post(root)
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["generation"] == workspace["generation"]
            receipt["resume_ms"] = round((time.perf_counter() - start) * 1000, 2)
            read_start = time.perf_counter()
            persisted = await http.get(root + "/file", params={**params, "path": "io.txt"})
            receipt["resume_first_read_ms"] = round((time.perf_counter() - read_start) * 1000, 2)
            receipt["resume_to_file_ms"] = round((time.perf_counter() - start) * 1000, 2)
            assert persisted.status_code == 200, persisted.text
            assert base64.b64decode(persisted.json()["content_base64"]) == payload
            assert (
                service.repository.get(workspace["id"])["resource"]["resource_id"]
                == resource_before
            )
            assert next(iter(service.channels.entries.values())).channel is not cached
            resumed_channel = next(iter(service.channels.entries.values())).channel
            assert (
                resumed_channel.service_receipt["compute_epoch"] > service_receipt["compute_epoch"]
            )
            if provider_name == "fly":
                assert resumed_channel.service_receipt["boot_id"] != service_receipt["boot_id"]
            receipt["functional_result"] = "passed"
            receipt["latency_goal"] = {"per_operation_p50_ms": 300, "per_operation_p95_ms": 1000}
            assert all(
                v["p50_ms"] <= 300 and v["p95_ms"] <= 1000 for v in receipt["warm"].values()
            ), receipt["warm"]
            receipt["result"] = "runtime_passed_cleanup_pending"
    finally:
        cleanup_succeeded = False
        try:
            if provider_name == "fly":
                await cleanup_owned(entry, provider, receipt)
            else:
                await cleanup_e2b(entry, provider, receipt)
            cleanup_succeeded = True
        finally:
            try:
                await provider.aclose()
                if cleanup_succeeded and receipt["result"] == "runtime_passed_cleanup_pending":
                    receipt["result"] = "passed"
            finally:
                receipt["samples_ms"] = samples
                destination = os.environ.get("CLOUD_AGENT_IO_RECEIPT_DIR")
                if destination:
                    Path(destination, provider_name + "-io.json").write_text(
                        json.dumps(receipt, indent=2) + "\n"
                    )
                print(
                    "PROVIDER_IO_RECEIPT "
                    + json.dumps({k: v for k, v in receipt.items() if k != "samples_ms"})
                )
