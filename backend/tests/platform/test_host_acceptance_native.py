"""Local acceptance for device folders: the real host service under Node, the
real gateway and API under uvicorn, real PostgreSQL 17 + PostgREST.

Covers what one computer can show: preview files byte for byte through the
relay, a loopback latency baseline, and a gateway restart the host recovers
from by itself. It is not the two-computer, packaged-App acceptance.
"""

import base64
import socket
import statistics
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

import src.platform.hosts.router as hosts_router
from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.hosts.connection_auth import HostConnectionAuth
from src.platform.hosts.gateway import HostGateway
from src.platform.hosts.router import (
    get_connection_auth,
    get_gateway,
    get_host_service,
    require_host_devices_enabled,
    router,
)
from src.platform.hosts.service import HostService
from tests.platform.test_host_devices_native import (  # noqa: F401  (fixtures)
    DESKTOP,
    HostDriver,
    database,
    repository,
    users,
)

pytestmark = pytest.mark.skipif(not DESKTOP, reason="cross-repository acceptance needs PUPPYONE_DESKTOP_ROOT")

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000d4944415478da63f8ffff3f0005fe02fea7d6a4c50000000049454e44ae426082")
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def serve_on(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline and thread.is_alive(), "API did not start"
        time.sleep(0.05)
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(10)


def api_for(owner, repository, gateway):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_host_devices_enabled] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=owner, role="authenticated")
    app.dependency_overrides[get_host_service] = lambda: HostService(repository)
    app.dependency_overrides[get_connection_auth] = lambda: HostConnectionAuth(repository)
    app.dependency_overrides[get_gateway] = lambda: gateway
    return app


def percentile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def test_previews_latency_and_gateway_restart_through_the_real_relay(database, repository, monkeypatch, tmp_path):
    owner = str(uuid4())
    users(database, owner)
    monkeypatch.setattr(settings, "HOST_DEVICES_ENABLED", True)
    shared = tmp_path / "Shared"
    shared.mkdir()
    files = {"image.png": PNG, "doc.pdf": PDF, "table.csv": b"a,b\n1,\"x,y\"\n",
             "note.md": "# Title\n\nUnicode: 中文\n".encode()}
    for name, data in files.items():
        (shared / name).write_bytes(data)
    state = tmp_path / "host-state"
    state.mkdir()
    port = free_port()
    base = f"http://127.0.0.1:{port}/api/v1"
    env = {"PATH": __import__("os").environ["PATH"], "HOME": str(tmp_path), "HOST_E2E_API": base,
           "HOST_E2E_USER": owner, "HOST_E2E_STATE": str(state), "HOST_E2E_FOLDER": str(shared.resolve())}

    first = HostGateway(repository)
    monkeypatch.setattr(hosts_router, "_gateway", first)
    host = None
    try:
        with serve_on(api_for(owner, repository, first), port), \
                httpx.Client(base_url=base, trust_env=False, timeout=30) as access:
            host = HostDriver(env)
            folder = host.call("share", writable=True)["folderId"]
            path = f"/hosts/folders/{folder}"

            # Preview files arrive byte for byte.
            for name, data in files.items():
                read = access.get(f"{path}/file", params={"path": name})
                assert read.status_code == 200, read.text
                assert base64.b64decode(read.json()["content_base64"]) == data

            # Loopback baseline for small reads and writes through API, gateway and host.
            reads, writes = [], []
            revision = access.get(f"{path}/file", params={"path": "note.md"}).json()["revision"]
            for index in range(30):
                started = time.perf_counter()
                assert access.get(f"{path}/file", params={"path": "note.md"}).status_code == 200
                reads.append((time.perf_counter() - started) * 1000)
                body = f"edit {index}".encode()
                started = time.perf_counter()
                saved = access.put(f"{path}/file", json={"path": "note.md",
                    "content_base64": base64.b64encode(body).decode(), "expected_revision": revision})
                writes.append((time.perf_counter() - started) * 1000)
                assert saved.status_code == 200, saved.text
                revision = saved.json()["revision"]
            print(f"\nloopback read p50={statistics.median(reads):.1f}ms p95={percentile(reads, 0.95):.1f}ms; "
                  f"write p50={statistics.median(writes):.1f}ms p95={percentile(writes, 0.95):.1f}ms")
            # The cross-network targets are p50 <= 300 ms and p95 <= 1 s; loopback must be far inside them.
            assert percentile(reads, 0.95) < 1000 and percentile(writes, 0.95) < 1000

        # The gateway process goes away and a new one starts on the same address.
        second = HostGateway(repository)
        monkeypatch.setattr(hosts_router, "_gateway", second)
        with serve_on(api_for(owner, repository, second), port), \
                httpx.Client(base_url=base, trust_env=False, timeout=30) as access:
            deadline = time.monotonic() + 40
            while True:
                read = access.get(f"{path}/file", params={"path": "image.png"})
                if read.status_code == 200:
                    break
                assert time.monotonic() < deadline, f"host did not come back: {read.status_code} {read.text}"
                time.sleep(0.5)
            assert base64.b64decode(read.json()["content_base64"]) == PNG
            [listed] = access.get("/hosts").json()["devices"]
            assert listed["online"] is True
            # Nothing was re-registered: the same device and folder came back.
            assert [entry["id"] for entry in listed["folders"]] == [folder]
            assert host.call("state")["identities"] == 1
    finally:
        if host:
            host.close()
