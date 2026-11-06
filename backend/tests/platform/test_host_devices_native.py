"""Real PostgreSQL 17, real PostgREST and the real HostRepository behind the hosts API.

Supplementary evidence only: one API process and a simulated device in this
process. The Desktop's host and SSH client are accepted across two computers
against the real deployment (Desktop tests/acceptance/two-computers).
"""

import base64
import importlib.util
import shutil
import subprocess
import time
from contextlib import ExitStack, suppress
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
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
    # No plaintext file route exists: a folder's content travels only inside SSH, through a tunnel.
    assert client.get(f"/api/v1/hosts/folders/{folder}/file?path=note.md").status_code == 404

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

    # Another account sees nothing.
    actor["id"] = stranger
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
    # Narrowing a folder to read-only applies to the next request; a stale editor is fenced.
    [listed] = client.get("/api/v1/hosts").json()["devices"]
    revision = next(item for item in listed["folders"] if item["id"] == folder)["revision"]
    narrowed = client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}",
                            json={"name": "Notes", "writable": False, "expected_revision": revision})
    assert narrowed.status_code == 200 and narrowed.json()["revision"] == revision + 1
    assert client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}",
                        json={"name": "Renamed", "writable": True, "expected_revision": revision}).status_code == 409
    assert client.patch(f"/api/v1/hosts/devices/{device}/folders/{folder}",
        json={"name": "Notes", "writable": True, "expected_revision": revision + 1}).status_code == 200

    audit = database.sql(
        f"SELECT string_agg(operation||':'||outcome, ',' ORDER BY id) FROM public.host_file_events "
        f"WHERE device_id='{device}';")
    # What happens inside a tunnel is never recorded; nothing here even passes through the Cloud.
    assert audit is None or audit == ""
    assert database.sql(
        "SELECT count(*) FROM public.host_file_events WHERE operation IN "
        "('list','read','write','mkdir','move','remove','git');") == "0"
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
    assert device_challenge(client, owner, device, key).status_code == 403
    assert client.get("/api/v1/hosts").json()["devices"] == []


def eventually(check, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(0.1)


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


def test_connection_records_are_kept_thirty_days(database, repository):
    owner = str(uuid4())
    users(database, owner)
    device = database.sql(f"SET ROLE service_role; SELECT public.host_device_register('{owner}', "
                          "decode(repeat('cd',32),'hex'), 'Mac', 'darwin');").splitlines()[-1]
    folder = str(uuid4())
    database.sql(f"SET ROLE service_role; SELECT public.host_folder_register('{owner}', '{device}', '{folder}', "
                 "'Notes', ARRAY['folder.read']);")
    database.sql("INSERT INTO public.host_file_events(device_id,folder_id,actor_user_id,operation,outcome,created_at) "
                 f"VALUES ('{device}','{folder}','{owner}','tunnel','succeeded', now() - interval '31 days'),"
                 f"('{device}','{folder}','{owner}','tunnel','succeeded', now() - interval '29 days');")
    repository.command("host_file_event_record", p_actor=owner, p_device=device, p_folder=folder,
                       p_operation="watch", p_outcome="succeeded")
    kept = database.sql("SELECT string_agg(operation || ':' || (created_at > now() - interval '30 days')::text, ',' "
                        f"ORDER BY id) FROM public.host_file_events WHERE device_id='{device}';")
    assert kept == "tunnel:true,watch:true"
