"""Opt-in opening latency with a real Fly Machine and owned PG/S3 fixtures.

The lifecycle and API have separate channel pools, as in deployment. Every
resume uses the product suspend/reconcile path; teardown deletes only this
fixture's Machine and Volume. First allocation/clone is reported separately.
"""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from src.config import settings
from src.platform.access.composition import build_workbench_service
from src.platform.workspace.execution.providers.fly import FlyRuntimeProvider
from tests.agent.workbench_acceptance.accept_fly import cleanup_owned
from tests.agent.workbench_acceptance.accept_io import summary
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
    services as services,
)
from tests.agent.workbench_acceptance.provider_scenario import http_server

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("CLOUD_AGENT_TEST_OPENING") != "1", reason="explicit Fly opt-in"
    ),
]


async def test_opening_latency(entry, monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROVIDER", "fly")
    provider = FlyRuntimeProvider()
    worker = build_workbench_service(engine=entry.engine)
    lifecycle = asyncio.create_task(worker.lifecycle.serve())
    receipt = {
        "result": "failed",
        "workload": "authenticated local HTTP; owned local PG/S3; real Fly; separate worker/API file pools",
        "samples": {},
    }
    try:
        async with http_server(entry.app, entry.http.headers) as http:
            root = f"/api/v1/projects/{entry.project}/workbench"

            async def open_root():
                start = time.perf_counter()
                replies = 0
                status = await http.get(root)
                assert status.status_code == 200, status.text
                result = status.json()
                row = result["workspace"]
                if not row or row["state"] != "running":
                    response = await http.post(root)
                    assert response.status_code in {200, 202}, response.text
                    row = response.json()
                    while row["state"] != "running" or row["operation"]["pending"]:
                        assert not row["operation"]["error"], row
                        response = await http.get(root, params={"wait_seconds": 20})
                        assert response.status_code == 200, response.text
                        result = response.json()
                        row = result["workspace"]
                        replies += 1
                if "entries" not in result:
                    response = await http.get(
                        root + "/files", params={"generation": row["generation"]}
                    )
                    assert response.status_code == 200, response.text
                    result = response.json()
                assert "seed.txt" in {item["name"] for item in result["entries"]}
                return row, round((time.perf_counter() - start) * 1000, 2), replies

            row, elapsed, _ = await open_root()
            receipt["initial_clone_ms"] = elapsed
            api = entry.app.state.workbench_service
            for mode, count in [
                ("warm", 10),
                ("reconnect", 5),
                ("resume", 3),
                ("cold_transport_resume", 3),
            ]:
                samples = []
                for _ in range(count):
                    if mode != "warm":
                        await api.channels.invalidate(row["id"])
                    if mode in {"resume", "cold_transport_resume"}:
                        current = await asyncio.to_thread(api.repository.get, row["id"])
                        claimed = await asyncio.to_thread(
                            api.repository.lifecycle_claim, current, suspend=True
                        )
                        assert claimed["acquired"]
                        await worker.lifecycle.pause(claimed["workbench"])
                        paused = await asyncio.to_thread(api.repository.get, row["id"])
                        assert paused["state"] == "paused"
                        if mode == "cold_transport_resume":
                            # Drop only this fixture process's caches. Production
                            # API/worker transports live in different processes.
                            await provider.shutdown_transports()
                    row, elapsed, polls = await open_root()
                    samples.append(elapsed)
                    print(
                        json.dumps({"mode": mode, "elapsed_ms": elapsed, "status_polls": polls}),
                        flush=True,
                    )
                receipt["samples"][mode] = {**summary(samples), "milliseconds": samples}
            receipt["result"] = "passed"
    finally:
        lifecycle.cancel()
        await asyncio.gather(lifecycle, return_exceptions=True)
        await worker.close()
        await cleanup_owned(entry, provider, receipt)
        await provider.aclose()
        path = Path(
            os.environ.get(
                "CLOUD_AGENT_OPENING_REPORT", "/tmp/puppy-workspace-opening-acceptance.json"
            )
        )
        path.write_text(json.dumps(receipt, indent=2) + "\n")
