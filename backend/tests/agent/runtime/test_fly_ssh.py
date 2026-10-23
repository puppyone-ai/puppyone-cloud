"""Connection reuse, credentials and cancellation are provider-local concerns."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest
from cryptography.hazmat.primitives.serialization import SSHCertificateBuilder, SSHCertificateType

from src.platform.workspace.execution.providers.fly_certificate import import_user_certificate
from src.platform.workspace.execution.providers.fly_ssh import FlySSHConnections
from src.platform.workspace.execution.worker_port import WorkerDisconnected


def certificate(key, *, app="owned", critical=b"allowed-apps@fly.io", expires=None):
    ca = asyncssh.generate_private_key("ssh-ed25519")
    return (
        SSHCertificateBuilder()
        .public_key(key.pyca_key.public_key())
        .type(SSHCertificateType.USER)
        .valid_principals([b"root", b"fly"])
        .valid_after(int(time.time()) - 60)
        .valid_before(expires or int(time.time()) + 3600)
        .add_critical_option(critical, json.dumps([app]).encode())
        .add_extension(b"jugslip-ticket@fly.io", b"opaque-provider-ticket")
        .sign(ca.pyca_key)
        .public_bytes()
        .decode()
    )


def test_certificate_preserves_signed_app_restriction_and_extensions():
    key = asyncssh.generate_private_key("ssh-ed25519")
    value = certificate(key)
    parsed = import_user_certificate(value, key, "owned")
    assert parsed.export_certificate().strip() == value.encode()
    asyncssh.load_keypairs([(key, parsed)])  # accepted without modifying signed bytes


@pytest.mark.parametrize(
    "change", [{"app": "other"}, {"critical": b"unknown"}, {"expires": int(time.time()) - 1}]
)
def test_certificate_scope_unknown_restriction_and_expiry_fail_closed(change):
    key = asyncssh.generate_private_key("ssh-ed25519")
    with pytest.raises(ValueError):
        import_user_certificate(certificate(key, **change), key, "owned")


def test_certificate_is_bound_to_generated_key():
    key = asyncssh.generate_private_key("ssh-ed25519")
    with pytest.raises(ValueError, match="key mismatch"):
        import_user_certificate(
            certificate(key), asyncssh.generate_private_key("ssh-ed25519"), "owned"
        )


def connection():
    return SimpleNamespace(
        is_closed=Mock(return_value=False),
        close=Mock(),
        wait_closed=AsyncMock(),
        create_process=AsyncMock(return_value=object()),
    )


@pytest.mark.asyncio
async def test_concurrent_channels_share_one_ssh_connection_and_idle_eviction(monkeypatch):
    pool = FlySSHConnections("owned", "token", "fly", {}, limit=1)
    opened = []

    async def connect(entry, _):
        await asyncio.sleep(0)
        entry.ssh = connection()
        entry.proxy = SimpleNamespace(
            returncode=None, stdin=SimpleNamespace(close=Mock()), wait=AsyncMock()
        )
        opened.append(entry.ssh)

    monkeypatch.setattr(pool, "_connect", connect)
    resource = {"app": "owned", "resource_id": "first"}
    try:
        leases = await asyncio.gather(
            *(pool.open(resource, "fdaa::1", "file-client") for _ in range(8))
        )
        assert len(opened) == 1 and opened[0].create_process.await_count == 8
        with pytest.raises(WorkerDisconnected, match="capacity"):
            await pool.open({**resource, "resource_id": "second"}, "fdaa::2", "file-client")
        for lease in leases:
            lease.release()
            lease.release()  # release is idempotent
        replacement = await pool.open(
            {**resource, "resource_id": "second"}, "fdaa::2", "file-client"
        )
        assert len(opened) == 2
        opened[0].close.assert_called_once()
        replacement.release()
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource,address",
    [({"app": "other"}, "fdaa::1"), ({"app": "owned"}, "8.8.8.8"), ({"app": "owned"}, None)],
)
async def test_wrong_app_and_non_private_target_never_connect(resource, address):
    pool = FlySSHConnections("owned", "token", "fly", {})
    pool._connect = AsyncMock()
    try:
        with pytest.raises(PermissionError):
            await pool.open(resource, address, "command")
        pool._connect.assert_not_awaited()
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_cancelled_connection_closes_partial_transport_and_releases_capacity():
    pool = FlySSHConnections("owned", "token", "fly", {})
    connected = asyncio.Event()
    partial = connection()

    async def connect(entry, _):
        entry.ssh = partial
        connected.set()
        await asyncio.Event().wait()

    pool._connect = connect
    task = asyncio.create_task(pool.open({"app": "owned"}, "fdaa::1", "command"))
    try:
        await connected.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        partial.close.assert_called_once()
        assert next(iter(pool.entries.values())).users == 0
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_new_compute_epoch_reconnects_ssh_without_discarding_private_route():
    pool = FlySSHConnections("owned", "token", "fly", {})
    proxy = SimpleNamespace(returncode=None, stdin=SimpleNamespace(close=Mock()), wait=AsyncMock())
    connections = []

    async def connect(entry, _):
        if entry.proxy:
            assert entry.proxy is proxy
            proxy.stdin.close.assert_not_called()
        entry.proxy = proxy
        entry.ssh = connection()
        connections.append(entry.ssh)

    pool._connect = connect
    resource = {"app": "owned"}
    try:
        first = await pool.open(resource, "fdaa::1", "files", epoch=1)
        first.release()
        second = await pool.open(resource, "fdaa::1", "files", epoch=2)
        second.release()
        assert len(connections) == 2
        connections[0].close.assert_called_once()
        proxy.stdin.close.assert_not_called()
    finally:
        await pool.close()
