"""Shared by the native hosts tests: a real PostgreSQL 17 database, the owner, and a simulated device."""

import base64
import shutil
import socket as socket_module
import subprocess
import threading
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import uuid4

import uvicorn

from src.platform.hosts.connection_auth import device_request_message, proof_message

ROOT = Path(__file__).resolve().parents[5]
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


# --- the API and the device_gateway as two real HTTP servers ---
SECRET = "f" * 48


def free_port():
    with socket_module.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="on", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline and thread.is_alive(), "server did not start"
        time.sleep(0.05)
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)
