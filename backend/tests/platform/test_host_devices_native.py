"""Real PostgreSQL 17, real PostgREST and the real HostRepository behind the hosts API.

Supplementary evidence only: one API process, plus either a simulated device in
this process or (with PUPPYONE_DESKTOP_ROOT) the Desktop host modules under plain
Node. It is not a deployed, multi-replica or two-packaged-App acceptance.
"""

import base64
import hashlib
import importlib.util
import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.platform.hosts.router as hosts_router
from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.hosts.connection_auth import HostConnectionAuth, device_request_message, proof_message
from src.platform.hosts.gateway import CLOSE_REPLACED, CLOSE_REVOKED, CLOSE_UNSUPPORTED, HostGateway
from src.platform.hosts.repository import HOST_COMMANDS, HostRepository
from src.platform.hosts.router import (
    get_connection_auth,
    get_gateway,
    get_host_service,
    require_host_devices_enabled,
    router,
)
from src.platform.hosts.service import HostService

ROOT = Path(__file__).resolve().parents[3]
PSQL = str(
    Path(shutil.which("postgres") or "/opt/homebrew/opt/postgresql@17/bin/postgres").with_name("psql")
)
REVISION = "a" * 64


class Database:
    def __init__(self, url):
        self.url = url

    def sql(self, statement):
        result = subprocess.run([PSQL, self.url, "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
                                input=statement, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()


@pytest.fixture(scope="module")
def database():
    spec = importlib.util.spec_from_file_location(
        "host_native_postgres", ROOT / "scripts/testing/native_postgres.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.native_postgres() as cluster:
        yield Database(cluster["url"])


@pytest.fixture(scope="module")
def repository(database):
    """Actual PostgREST as service_role against the owned database, never a remote service."""
    from postgrest import SyncPostgrestClient

    from src.infra.supabase.instrumentation import DatabaseTransport
    from tests.agent.runtime.owned_http import owned_http_service

    with (owned_http_service("postgrest", database_url=database.url) as url,
          httpx.Client(trust_env=False, transport=DatabaseTransport(path_prefix="/")) as http):
        yield HostRepository(SyncPostgrestClient(url, http_client=http))


@pytest.fixture
def api(repository, monkeypatch):
    actor = {"id": None}
    gateway = HostGateway(repository)
    monkeypatch.setattr(settings, "HOST_DEVICES_ENABLED", True)
    monkeypatch.setattr(hosts_router, "_gateway", gateway)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_host_devices_enabled] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=actor["id"], role="authenticated")
    app.dependency_overrides[get_host_service] = lambda: HostService(repository)
    app.dependency_overrides[get_connection_auth] = lambda: HostConnectionAuth(repository)
    app.dependency_overrides[get_gateway] = lambda: gateway
    with TestClient(app) as client:
        yield client, actor


def users(database, *identities):
    database.sql("INSERT INTO auth.users(id) VALUES " + ",".join(f"('{value}')" for value in identities) + ";")


BOOT = str(uuid4())


def device_challenge(client, owner, device, key):
    """A challenge requested the way a host does: signed by its device key, no human JWT."""
    timestamp = int(time.time())
    signature = base64.b64encode(key.sign(device_request_message(owner, device, timestamp))).decode()
    return client.post(f"/api/v1/hosts/devices/{device}/device-challenge",
                       json={"owner": owner, "timestamp": timestamp, "signature": signature})


def connect(client, sockets, owner, device, key, protocol=1):
    challenge = device_challenge(client, owner, device, key)
    assert challenge.status_code == 200, challenge.text
    body = challenge.json()
    signature = key.sign(proof_message(owner, device, body["challenge_id"], body["epoch"], body["nonce"]))
    session = client.websocket_connect(f"/api/v1/hosts/devices/{device}/connect")
    socket = session.__enter__()
    # The gateway closes these itself (replacement, revocation); exit best-effort.
    sockets.callback(_exit, session)
    socket.send_json({"owner": owner, "challenge_id": body["challenge_id"], "epoch": body["epoch"],
                      "nonce": body["nonce"], "signature": base64.b64encode(signature).decode(),
                      "protocol": protocol, "boot_id": BOOT, "app_version": "1.4.0"})
    if protocol != 1:
        return socket, body
    assert socket.receive_json() == {"type": "ready", "epoch": body["epoch"]}
    return socket


def _exit(session):
    with suppress(Exception):
        session.__exit__(None, None, None)


def serve_one(client, socket, request, reply):
    """Run the HTTP call while the simulated device answers exactly one frame."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(request)
        frame = socket.receive_json()
        socket.send_json({"type": "reply", "id": frame["id"], **reply(frame)})
        return frame, pending.result(timeout=10)


def test_real_database_routes_audits_fences_and_revokes(database, api):
    with ExitStack() as sockets:
        _routes_audits_fences_and_revokes(database, *api, sockets)


def _routes_audits_fences_and_revokes(database, client, actor, sockets):
    owner, stranger = str(uuid4()), str(uuid4())
    users(database, owner, stranger)
    actor["id"] = owner
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

    registered = client.post("/api/v1/hosts/devices", json={"public_key": public, "name": "Mac", "platform": "darwin"})
    assert registered.status_code == 201, registered.text
    device = registered.json()["device_id"]
    # A lost registration reply retries to the same identity.
    assert client.post("/api/v1/hosts/devices", json={"public_key": public, "name": "Mac",
                                                      "platform": "darwin"}).json()["device_id"] == device
    folder, readonly = str(uuid4()), str(uuid4())
    assert client.post(f"/api/v1/hosts/devices/{device}/folders",
                       json={"folder_id": folder, "name": "Notes", "writable": True}).status_code == 201
    assert client.post(f"/api/v1/hosts/devices/{device}/folders",
                       json={"folder_id": readonly, "name": "Reference"}).status_code == 201

    [listed] = client.get("/api/v1/hosts").json()["devices"]
    assert (listed["online"], listed["last_seen_at"], len(listed["folders"])) == (False, None, 2)
    offline = client.get(f"/api/v1/hosts/folders/{folder}/file?path=note.md")
    assert (offline.status_code, offline.json()["detail"]) == (503, {"code": "device_offline"})

    # A host speaking a protocol this gateway does not serve is told to update,
    # and its one-use challenge is left unconsumed rather than burned.
    outdated, attempt = connect(client, sockets, owner, device, key, protocol=2)
    with pytest.raises(WebSocketDisconnect) as unsupported:
        outdated.receive_json()
    assert unsupported.value.code == CLOSE_UNSUPPORTED
    assert database.sql(f"SELECT used_at IS NULL FROM public.host_connection_challenges "
                        f"WHERE id='{attempt['challenge_id']}';") == "t"
    assert client.get("/api/v1/hosts").json()["devices"][0]["online"] is False

    socket = connect(client, sockets, owner, device, key)
    [listed] = client.get("/api/v1/hosts").json()["devices"]
    assert listed["online"] is True and listed["last_seen_at"]
    assert (listed["protocol"], listed["app_version"]) == (1, "1.4.0")
    assert database.sql(f"SELECT boot_id FROM public.host_connections WHERE device_id='{device}';") == BOOT

    frame, read = serve_one(client, socket,
        lambda: client.get(f"/api/v1/hosts/folders/{folder}/file?path=note.md"),
        lambda _: {"result": {"content_base64": "bm90ZQ==", "revision": REVISION}})
    assert (frame["action"], frame["folder_id"], frame["path"]) == ("read", folder, "note.md")
    assert read.status_code == 200 and read.json()["revision"] == REVISION

    _, conflict = serve_one(client, socket,
        lambda: client.put(f"/api/v1/hosts/folders/{folder}/file",
                           json={"path": "note.md", "content_base64": "bmV3", "expected_revision": REVISION}),
        lambda _: {"error": "conflict"})
    assert (conflict.status_code, conflict.json()["detail"]) == (409, {"code": "conflict"})
    _, missing = serve_one(client, socket,
        lambda: client.get(f"/api/v1/hosts/folders/{folder}/file?path=gone.md"),
        lambda _: {"error": "not_found"})
    assert missing.status_code == 404
    _, opaque = serve_one(client, socket,
        lambda: client.get(f"/api/v1/hosts/folders/{folder}/files"),
        lambda _: {"error": {"path": "/Users/private"}})
    assert (opaque.status_code, opaque.json()["detail"]) == (400, {"code": "file_operation_failed"})

    # Write capability and other accounts are refused before any frame reaches the device.
    denied_write = client.put(f"/api/v1/hosts/folders/{readonly}/file",
                              json={"path": "note.md", "content_base64": "bmV3", "expected_revision": REVISION})
    assert denied_write.status_code == 403
    actor["id"] = stranger
    assert client.get(f"/api/v1/hosts/folders/{folder}/file?path=note.md").status_code == 403
    assert client.get("/api/v1/hosts").json()["devices"] == []
    # Claiming someone else's device is a definite denial.
    assert device_challenge(client, stranger, device, key).status_code == 403
    actor["id"] = owner

    socket.send_json({"type": "heartbeat"})
    assert socket.receive_json() == {"type": "heartbeat_ack"}

    # A reconnect replaces the old epoch; the old socket is closed and never served again.
    replacement = connect(client, sockets, owner, device, key)
    with pytest.raises(WebSocketDisconnect) as replaced:
        socket.receive_json()
    assert replaced.value.code == CLOSE_REPLACED
    entries = {"entries": [{"name": "note.md", "kind": "file", "size": 4, "modified_ns": "1"}]}
    _, routed = serve_one(client, replacement,
        lambda: client.get(f"/api/v1/hosts/folders/{folder}/files?path="),
        lambda _: {"result": entries})
    assert routed.json() == entries

    # Directory operations need write access and carry only folder-relative paths.
    frame, moved = serve_one(client, replacement,
        lambda: client.post(f"/api/v1/hosts/folders/{folder}/files",
                            json={"action": "move", "path": "note.md", "destination": "notes/note.md"}),
        lambda _: {"result": {}})
    assert (frame["action"], frame["path"], frame["destination"]) == ("move", "note.md", "notes/note.md")
    assert moved.status_code == 200
    assert client.post(f"/api/v1/hosts/folders/{folder}/files",
                       json={"action": "move", "path": "note.md"}).status_code == 422
    assert client.post(f"/api/v1/hosts/folders/{readonly}/files",
                       json={"action": "mkdir", "path": "new"}).status_code == 403
    frame, created = serve_one(client, replacement,
        lambda: client.put(f"/api/v1/hosts/folders/{folder}/file",
                           json={"path": "fresh.md", "content_base64": "bmV3", "expected_revision": None}),
        lambda _: {"error": "conflict"})
    assert frame["expected_revision"] is None and created.status_code == 409

    # Narrowing a folder to read-only applies to the next request; a stale editor is fenced.
    [listed] = client.get("/api/v1/hosts").json()["devices"]
    revision = next(item for item in listed["folders"] if item["id"] == folder)["revision"]
    narrowed = client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}",
                            json={"name": "Notes", "writable": False, "expected_revision": revision})
    assert narrowed.status_code == 200 and narrowed.json()["revision"] == revision + 1
    assert client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}",
                        json={"name": "Renamed", "writable": True, "expected_revision": revision}).status_code == 409
    assert client.put(f"/api/v1/hosts/folders/{folder}/file", json={"path": "note.md",
        "content_base64": "bmV3", "expected_revision": REVISION}).status_code == 403
    assert client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}",
        json={"name": "Notes", "writable": True, "expected_revision": revision + 1}).status_code == 200

    audit = database.sql(
        f"SELECT string_agg(operation||':'||outcome, ',' ORDER BY id) FROM public.host_file_events "
        f"WHERE device_id='{device}';")
    assert audit == ("read:intended,read:succeeded,write:intended,write:failed,"
                     "read:intended,read:failed,list:intended,list:failed,list:intended,list:succeeded,"
                     "move:intended,move:succeeded,write:intended,write:failed")
    assert database.sql(
        "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='host_file_events';"
    ) == "id,device_id,folder_id,actor_user_id,operation,outcome,created_at"
    # Issuing each challenge pruned the used ones: the last used challenge and the
    # still-valid one the outdated host never consumed remain, nothing older.
    assert database.sql(
        f"SELECT count(*) FROM public.host_connection_challenges WHERE device_id='{device}';") == "2"

    revoked = client.delete(f"/api/v1/hosts/devices/{device}")
    assert revoked.json() == {"revoked": True}
    with pytest.raises(WebSocketDisconnect) as closed:
        replacement.receive_json()
    assert closed.value.code == CLOSE_REVOKED
    assert client.get(f"/api/v1/hosts/folders/{folder}/file?path=note.md").status_code == 403
    assert device_challenge(client, owner, device, key).status_code == 403
    assert client.get("/api/v1/hosts").json()["devices"] == []


DESKTOP = os.environ.get("PUPPYONE_DESKTOP_ROOT")


@contextmanager
def serve(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off", timeout_graceful_shutdown=2))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline and thread.is_alive(), "API did not start"
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/api/v1"
    finally:
        server.should_exit = True
        thread.join(10)


class HostDriver:
    """The Desktop host modules under Node, speaking JSON lines; see hostDeviceDriver.mjs."""

    def __init__(self, env):
        self.process = subprocess.Popen(
            ["node", "tests/support/cloud/hostDeviceDriver.mjs"], cwd=DESKTOP, env=env, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines = queue.Queue()
        self.events = []
        self.sequence = 0
        threading.Thread(target=self._read, daemon=True).start()
        assert self.wait_event("started", 15)

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(json.loads(line))

    def _next(self, timeout):
        message = self.lines.get(timeout=timeout)
        if "event" in message:
            self.events.append(message)
        return message

    def call(self, command, timeout=30, **arguments):
        self.sequence += 1
        self.process.stdin.write(json.dumps({"id": self.sequence, "command": command, **arguments}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            message = self._next(max(0.1, deadline - time.monotonic()))
            if message.get("id") == self.sequence:
                assert message["ok"], message.get("error")
                return message["result"]

    def wait_event(self, name, timeout=15):
        deadline = time.monotonic() + timeout
        while not any(event["event"] == name for event in self.events):
            try:
                self._next(max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                return None
        return next(event for event in self.events if event["event"] == name)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def _next_event(lines):
    """The next SSE event as (name, data); keepalive comments are skipped."""
    name, data = None, []
    for line in lines:
        if line.startswith(":"):
            continue
        if not line:
            if name:
                return name, json.loads("\n".join(data) or "{}")
            continue
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    raise AssertionError("event stream ended")


def eventually(check, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(0.1)


@pytest.mark.skipif(not DESKTOP, reason="cross-repository acceptance needs PUPPYONE_DESKTOP_ROOT")
def test_desktop_host_modules_serve_a_folder_through_the_real_gateway(database, repository, monkeypatch, tmp_path):
    owner = str(uuid4())
    users(database, owner)
    gateway = HostGateway(repository)
    monkeypatch.setattr(settings, "HOST_DEVICES_ENABLED", True)
    monkeypatch.setattr(hosts_router, "_gateway", gateway)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_host_devices_enabled] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=owner, role="authenticated")
    app.dependency_overrides[get_host_service] = lambda: HostService(repository)
    app.dependency_overrides[get_connection_auth] = lambda: HostConnectionAuth(repository)
    app.dependency_overrides[get_gateway] = lambda: gateway

    shared = tmp_path / "Shared Notes"
    (shared / "sub").mkdir(parents=True)
    (shared / "note.md").write_text("hello")
    (shared / "sub" / "inner.md").write_text("inner")
    (shared / ".git").mkdir()
    (shared / ".git" / "config").write_text("private")
    (shared / "large.bin").write_bytes(b"\0" * (8 * 1024 * 1024 + 1))
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    (shared / "escape.md").symlink_to(outside)
    state = tmp_path / "desktop-state"
    state.mkdir()

    with serve(app) as api, httpx.Client(base_url=api, trust_env=False, timeout=30) as access:
        env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "HOST_E2E_API": api,
               "HOST_E2E_USER": owner, "HOST_E2E_STATE": str(state),
               "HOST_E2E_FOLDER": str(shared.resolve())}
        host = HostDriver(env)
        try:
            shared_ids = host.call("share", writable=True)
            device, folder = shared_ids["deviceId"], shared_ids["folderId"]
            files = f"/hosts/folders/{folder}"

            [listed] = access.get("/hosts").json()["devices"]
            assert listed["id"] == device and listed["online"] is True
            assert [entry["id"] for entry in listed["folders"]] == [folder]
            # The backend never learns the host path.
            assert str(shared.resolve()) not in access.get("/hosts").text
            assert database.sql("SELECT count(*) FROM public.host_folders WHERE display_name='Shared Notes';") == "1"

            listing = access.get(f"{files}/files", params={"path": ""}).json()["entries"]
            assert [(entry["name"], entry["kind"]) for entry in listing] == [
                ("sub", "directory"), ("large.bin", "file"), ("note.md", "file")]
            assert [entry["name"] for entry in
                    access.get(f"{files}/files", params={"path": "sub"}).json()["entries"]] == ["inner.md"]

            read = access.get(f"{files}/file", params={"path": "note.md"}).json()
            assert base64.b64decode(read["content_base64"]) == b"hello"
            assert read["revision"] == hashlib.sha256(b"hello").hexdigest()

            saved = access.put(f"{files}/file", json={"path": "note.md",
                "content_base64": base64.b64encode(b"edited remotely").decode(),
                "expected_revision": read["revision"]})
            assert saved.status_code == 200, saved.text
            assert (shared / "note.md").read_bytes() == b"edited remotely"
            assert saved.json()["revision"] == hashlib.sha256(b"edited remotely").hexdigest()

            # A change made on the host is never silently overwritten by a stale save.
            (shared / "note.md").write_text("edited on the host")
            stale = access.put(f"{files}/file", json={"path": "note.md",
                "content_base64": base64.b64encode(b"late remote edit").decode(),
                "expected_revision": saved.json()["revision"]})
            assert (stale.status_code, stale.json()["detail"]) == (409, {"code": "conflict"})
            assert (shared / "note.md").read_text() == "edited on the host"

            for path, status, code in [("../outside.md", 400, "invalid_path"), ("escape.md", 400, "invalid_path"),
                                       (".git/config", 400, "invalid_path"), ("large.bin", 400, "unsupported_file"),
                                       ("missing.md", 404, "not_found"), ("sub/missing.md", 404, "not_found")]:
                refused = access.get(f"{files}/file", params={"path": path})
                assert (refused.status_code, refused.json()["detail"]) == (status, {"code": code}), path

            # Create, mkdir, move and remove run on the host, inside the folder only.
            created = access.put(f"{files}/file", json={"path": "sub/new.md",
                "content_base64": base64.b64encode(b"created").decode(), "expected_revision": None})
            assert created.status_code == 200 and (shared / "sub" / "new.md").read_bytes() == b"created"
            again = access.put(f"{files}/file", json={"path": "sub/new.md",
                "content_base64": base64.b64encode(b"other").decode(), "expected_revision": None})
            assert again.status_code == 409 and (shared / "sub" / "new.md").read_bytes() == b"created"
            assert access.post(f"{files}/files", json={"action": "mkdir", "path": "made"}).status_code == 200
            assert (shared / "made").is_dir()
            moved = access.post(f"{files}/files", json={"action": "move", "path": "sub/new.md",
                                                        "destination": "made/new.md"})
            assert moved.status_code == 200 and (shared / "made" / "new.md").exists()
            clobber = access.post(f"{files}/files", json={"action": "move", "path": "made/new.md",
                                                          "destination": "note.md"})
            assert (clobber.status_code, clobber.json()["detail"]) == (409, {"code": "already_exists"})
            assert access.post(f"{files}/files", json={"action": "remove", "path": "made"}).status_code == 200
            assert not (shared / "made").exists()
            escape = access.post(f"{files}/files", json={"action": "move", "path": "note.md",
                                                         "destination": "../stolen.md"})
            assert escape.status_code == 400 and not (tmp_path / "stolen.md").exists()

            # A change made on the host reaches the other computer as an event.
            with access.stream("GET", f"{files}/events", timeout=30) as events:
                assert events.status_code == 200
                lines = events.iter_lines()
                assert _next_event(lines) == ("change", {"paths": [], "reset": True})
                (shared / "sub" / "inner.md").write_text("changed on the host")
                # The OS may still deliver notices for the operations just above; wait for this one.
                for _ in range(10):
                    kind, value = _next_event(lines)
                    assert kind == "change"
                    if value["reset"] or "sub/inner.md" in value["paths"]:
                        break
                else:
                    raise AssertionError("host change never arrived")

            # Quitting the host App takes it offline at once; it does not fall back to a stale copy.
            host.close()
            assert eventually(lambda: access.get(f"{files}/file", params={"path": "note.md"}).status_code == 503)
            assert access.get("/hosts").json()["devices"][0]["online"] is False

            # Restarting reuses the same device identity and local folder mapping.
            host = HostDriver(env)
            assert host.call("resume") == {"deviceId": device}
            assert len(access.get("/hosts").json()["devices"]) == 1
            again = access.get(f"{files}/file", params={"path": "note.md"}).json()
            assert base64.b64decode(again["content_base64"]) == b"edited on the host"

            assert access.delete(f"/hosts/devices/{device}").json() == {"revoked": True}
            assert host.wait_event("revoked")["deviceId"] == device
            assert access.get(f"{files}/file", params={"path": "note.md"}).status_code == 403
            assert host.call("state") == {"identities": 0, "folders": [{"folderId": folder, "state": "removed"}]}
            assert database.sql(
                "SELECT count(*) FROM public.host_file_events WHERE outcome='unknown';") == "0"
        finally:
            host.close()


def test_terminals_and_agents_run_only_with_consent_and_end_on_withdrawal(database, api, monkeypatch):
    import src.platform.auth.dependencies as auth_dependencies
    import src.platform.hosts.execution as execution

    client, actor = api
    owner = str(uuid4())
    users(database, owner)
    actor["id"] = owner

    class Auth:
        def get_current_user(self, token):
            if token != "owner-token":
                raise ValueError("bad token")
            return CurrentUser(user_id=owner, role="authenticated")

    monkeypatch.setattr(auth_dependencies, "get_auth_service", lambda: Auth())
    monkeypatch.setattr(execution, "EXEC_RENEW_SECONDS", 0.2)
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    device = client.post("/api/v1/hosts/devices", json={"public_key": public, "name": "Mac",
                                                        "platform": "darwin"}).json()["device_id"]
    folder = str(uuid4())
    client.post(f"/api/v1/hosts/devices/{device}/folders", json={"folder_id": folder, "name": "Work", "writable": True})
    exec_path = f"/api/v1/hosts/folders/{folder}/exec"

    def folder_revision():
        [listed] = client.get("/api/v1/hosts").json()["devices"]
        return next(item for item in listed["folders"] if item["id"] == folder)["revision"]

    def attach(sockets, start, token="owner-token"):
        session = client.websocket_connect(exec_path, subprotocols=[f"version.bearer.{token}"])
        terminal = session.__enter__()
        sockets.callback(_exit, session)
        terminal.send_json(start)
        return terminal

    with ExitStack() as sockets:
        host = connect(client, sockets, owner, device, key)
        shell = {"type": "start", "kind": "shell", "launcher": "shell", "cols": 100, "rows": 30}

        # No server grant yet: nothing reaches the host.
        refused = attach(sockets, shell)
        assert refused.receive_json() == {"type": "error", "code": "not_allowed"}
        with pytest.raises(WebSocketDisconnect):
            attach(sockets, shell, token="stolen").receive_json()

        # Execution needs an editable folder.
        assert client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}", json={
            "name": "Work", "writable": False, "terminal": True, "expected_revision": folder_revision()
        }).status_code == 400
        granted = client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}", json={
            "name": "Work", "writable": True, "terminal": True, "agent": True, "expected_revision": folder_revision()})
        assert granted.status_code == 200, granted.text

        terminal = attach(sockets, shell)
        opened = host.receive_json()
        assert {key: opened[key] for key in ("type", "folder_id", "kind", "launcher", "cols", "rows")} == {
            "type": "exec_open", "folder_id": folder, "kind": "shell", "launcher": "shell", "cols": 100, "rows": 30}
        host.send_json({"type": "exec_opened", "session": opened["session"]})
        assert terminal.receive_json() == {"type": "started", "session": opened["session"]}
        terminal.send_json({"type": "input", "data": base64.b64encode(b"ls\n").decode()})
        assert host.receive_json() == {"type": "exec_input", "session": opened["session"], "data": "bHMK"}
        terminal.send_json({"type": "resize", "cols": 120, "rows": 40})
        assert host.receive_json()["type"] == "exec_resize"
        host.send_json({"type": "exec_output", "session": opened["session"], "data": "b3V0cHV0"})
        assert terminal.receive_json() == {"type": "output", "data": "b3V0cHV0"}
        host.send_json({"type": "exec_exit", "session": opened["session"], "status": 0})
        assert terminal.receive_json() == {"type": "exit", "status": 0}
        assert eventually(lambda: database.sql(
            f"SELECT state||':'||exit_status FROM public.host_exec_sessions WHERE id='{opened['session']}';")
            == "closed:0")

        # An Agent launcher the host does not have is reported, not guessed.
        agent = attach(sockets, {"type": "start", "kind": "agent", "launcher": "claude", "cols": 80, "rows": 24})
        opened = host.receive_json()
        assert (opened["kind"], opened["launcher"]) == ("agent", "claude")
        host.send_json({"type": "exec_failed", "session": opened["session"], "error": "launcher_unavailable"})
        assert agent.receive_json() == {"type": "error", "code": "launcher_unavailable"}
        bad = attach(sockets, {"type": "start", "kind": "agent", "launcher": "rm -rf /", "cols": 80, "rows": 24})
        assert bad.receive_json() == {"type": "error", "code": "invalid_start"}

        # Withdrawing the grant ends a running terminal at its next renewal.
        terminal = attach(sockets, shell)
        opened = host.receive_json()
        host.send_json({"type": "exec_opened", "session": opened["session"]})
        assert terminal.receive_json()["type"] == "started"
        assert client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}", json={
            "name": "Work", "writable": True, "expected_revision": folder_revision()}).status_code == 200
        assert terminal.receive_json() == {"type": "error", "code": "not_allowed"}
        assert host.receive_json() == {"type": "exec_close", "session": opened["session"]}

        # Revoking the device ends every session on it at once.
        client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}", json={
            "name": "Work", "writable": True, "terminal": True, "expected_revision": folder_revision()})
        terminal = attach(sockets, shell)
        opened = host.receive_json()
        host.send_json({"type": "exec_opened", "session": opened["session"]})
        assert terminal.receive_json()["type"] == "started"
        assert client.delete(f"/api/v1/hosts/devices/{device}").json() == {"revoked": True}
        assert terminal.receive_json() == {"type": "error", "code": "not_allowed"}

    audit = database.sql(
        "SELECT string_agg(operation||':'||outcome, ',' ORDER BY id) FROM public.host_file_events "
        f"WHERE device_id='{device}';")
    assert audit == ("shell:intended,shell:succeeded,agent:intended,agent:failed,"
                     "shell:intended,shell:succeeded,shell:intended,shell:succeeded")
    assert database.sql(
        f"SELECT count(*) FROM public.host_exec_sessions WHERE device_id='{device}' AND state='active';") == "0"


def test_a_host_comes_online_with_its_device_key_and_no_one_signed_in(database, api):
    client, actor = api
    owner, stranger = str(uuid4()), str(uuid4())
    users(database, owner, stranger)
    actor["id"] = owner
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    device = client.post("/api/v1/hosts/devices", json={"public_key": public, "name": "Mac",
                                                       "platform": "darwin"}).json()["device_id"]
    # From here on nobody is signed in: every call below carries only the device key.
    actor["id"] = None

    def ask(signer=key, owner_id=owner, timestamp=None):
        timestamp = int(time.time()) if timestamp is None else timestamp
        signature = base64.b64encode(signer.sign(device_request_message(owner_id, device, timestamp))).decode()
        return client.post(f"/api/v1/hosts/devices/{device}/device-challenge",
                           json={"owner": owner_id, "timestamp": timestamp, "signature": signature})

    issued = ask()
    assert issued.status_code == 200, issued.text
    body = issued.json()
    proof = key.sign(proof_message(owner, device, body["challenge_id"], body["epoch"], body["nonce"]))
    with ExitStack() as sockets:
        session = client.websocket_connect(f"/api/v1/hosts/devices/{device}/connect")
        socket = session.__enter__()
        sockets.callback(_exit, session)
        socket.send_json({"owner": owner, "challenge_id": body["challenge_id"], "epoch": body["epoch"],
                          "nonce": body["nonce"], "signature": base64.b64encode(proof).decode(),
                          "protocol": 1, "boot_id": BOOT, "app_version": "1.4.0"})
        assert socket.receive_json() == {"type": "ready", "epoch": body["epoch"]}
        assert database.sql(f"SELECT epoch FROM public.host_connections WHERE device_id='{device}';") == body["epoch"]

    # Another key, another owner's claim or a stale clock never issue a challenge, and never
    # look like revocation (403), which would make the host forget a key that is still valid.
    before = database.sql(f"SELECT count(*) FROM public.host_connection_challenges WHERE device_id='{device}';")
    forged = ask(signer=Ed25519PrivateKey.generate())
    assert (forged.status_code, forged.json()["detail"]) == (401, {"code": "device_proof_invalid"})
    claimed = ask(owner_id=stranger)
    assert (claimed.status_code, claimed.json()["detail"]) == (403, {"code": "device_revoked"})
    stale = ask(timestamp=int(time.time()) - 3600)
    assert stale.status_code == 401 and stale.json()["detail"]["code"] == "device_clock_skew"
    assert abs(stale.json()["detail"]["server_time"] - time.time()) < 60
    assert database.sql(
        f"SELECT count(*) FROM public.host_connection_challenges WHERE device_id='{device}';") == before

    # Revoked by its owner: a definite denial.
    actor["id"] = owner
    assert client.delete(f"/api/v1/hosts/devices/{device}").status_code == 200
    actor["id"] = None
    revoked = ask()
    assert (revoked.status_code, revoked.json()["detail"]) == (403, {"code": "device_revoked"})


def test_every_host_rpc_is_service_only(database, repository):
    for name in sorted(HOST_COMMANDS):
        privileges = database.sql(
            "SELECT string_agg(r||'='||has_function_privilege(r,p.oid,'EXECUTE'),',' ORDER BY r) "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace, "
            "unnest(ARRAY['anon','authenticated','service_role']) r "
            f"WHERE n.nspname='public' AND p.proname='{name}';")
        assert privileges == "anon=false,authenticated=false,service_role=true", name
    for table in ("host_devices", "host_folders", "host_connections",
                  "host_connection_challenges", "host_file_events"):
        assert database.sql(
            f"SELECT has_table_privilege('service_role','public.{table}','SELECT');") == "f", table
