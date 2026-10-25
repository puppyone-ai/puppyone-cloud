"""Sprites contracts against local REST/exec peers; never allocates hosted VMs."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from src.config import settings
from src.platform.sandbox.errors import WorkerDisconnected, WorkerLost, WorkspaceUnavailable
from src.platform.sandbox.port import RuntimeConnection, resource_identity
from src.platform.sandbox.providers.sprites.provider import SpritesRuntimeProvider
from src.platform.sandbox.providers.sprites.transport import SpritesTransport
from src.platform.sandbox.registry import runtime_provider
from src.platform.sandbox.store import InMemoryExecutionSessionStore
from src.platform.workspace.execution.workbench_host import WorkbenchChannel


@pytest.fixture
def configured(monkeypatch, tmp_path):
    bundle = tmp_path / "worker.tar.gz"
    bundle.write_bytes(b"fixture runtime bytes")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    monkeypatch.setattr(settings, "CLOUD_AGENT_SPRITES_TOKEN", "fixture-token")
    monkeypatch.setattr(settings, "CLOUD_AGENT_SPRITES_BUNDLE", str(bundle))
    monkeypatch.setattr(settings, "CLOUD_AGENT_SPRITES_SHA256", digest)
    generation = str(uuid4())
    return dict(
        id=str(uuid4()),
        generation=generation,
        project_id="fixture-project",
        compute_epoch=1,
        binding={"provider": "sprites", "artifact": "sha256:" + digest},
        resource={
            "provider": "sprites",
            "resource_id": "pw-" + generation.replace("-", ""),
            "project_id": "fixture-project",
            "workbench_id": "",
            "workbench_generation": generation,
            "image_id": "sha256:" + digest,
            "allocation_pending": False,
            "sprites_id": "unique-id",
        },
    )


def host_for(row):
    row["resource"]["workbench_id"] = row["id"]
    return SimpleNamespace(
        resource=row["resource"].copy(),
        artifact=row["binding"]["artifact"],
        restore_point=None,
        connection=None,
        attached=False,
    )


def metadata(resource, *, status="running"):
    return {
        "id": "unique-id",
        "name": resource["resource_id"],
        "status": status,
        "labels": SpritesRuntimeProvider._labels(resource),
    }


def test_registry_selection_does_not_rebind_existing_resources(configured, monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_AGENT_SANDBOX_PROVIDER", "sprites")
    assert runtime_provider().name == "sprites"
    assert runtime_provider("fly").name == "fly"
    assert runtime_provider("e2b").name == "e2b"
    assert runtime_provider().artifact("unused") == configured["binding"]["artifact"]


@pytest.mark.asyncio
@pytest.mark.parametrize("preexisting", [False, True])
async def test_allocation_creates_or_recovers_bootstrap_without_duplicate_vm(
    configured, preexisting
):
    host = host_for(configured)
    host.resource["allocation_pending"] = True
    host.resource.pop("sprites_id")
    calls = []
    created = preexisting

    def api(request):
        nonlocal created
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v1/sprites":
            assert not created
            assert json.loads(request.content) == {
                "name": host.resource["resource_id"],
                "labels": SpritesRuntimeProvider._labels(host.resource),
                "url_settings": {"auth": "sprite"},
            }
            created = True
            return httpx.Response(201, json=metadata(host.resource))
        if not created:
            return httpx.Response(404)
        if request.url.path.endswith("/services/puppyone-files"):
            if request.method == "PUT":
                assert json.loads(request.content)["cmd"] == "sudo"
                return httpx.Response(200, text='{"type":"started"}\n{"type":"complete"}\n')
            return httpx.Response(200, json={"state": {"status": "running"}})
        if request.url.path.endswith("/policy/network"):
            assert json.loads(request.content) == {"rules": [{"domain": "*", "action": "deny"}]}
            return httpx.Response(200, json={})
        return httpx.Response(200, json=metadata(host.resource))

    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
        transport = SpritesTransport("fixture-token", client=client)
        transport.execute = AsyncMock(return_value="")
        provider = SpritesRuntimeProvider(transport=transport)
        await provider.resume(host)
        assert host.resource["sprites_id"] == "unique-id"
        assert host.resource["allocation_pending"] is False
        assert calls.count(("POST", "/v1/sprites")) == (0 if preexisting else 1)
        upload = transport.execute.call_args_list[0]
        assert upload.kwargs["data"].endswith(b"fixture runtime bytes")
        assert len(upload.args[1][5]) < 200  # Installer body travels over stdin, not exec URL.
        assert upload.args[1][:4] == ["sudo", "-n", "--", "python3"]
        assert transport.execute.call_args_list[-1].args[1][-2:] == ["hold", host.artifact]


@pytest.mark.asyncio
async def test_invalid_bundle_fails_before_allocating(configured):
    host = host_for(configured)
    host.artifact = "sha256:" + "f" * 64
    host.resource["image_id"] = host.artifact
    transport = SimpleNamespace(request=AsyncMock())
    with pytest.raises(ValueError, match="digest"):
        await SpritesRuntimeProvider(transport=transport).allocate(host)
    transport.request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["owner", "id", "name"])
async def test_substituted_resources_are_rejected_before_exec_or_delete(configured, mutation):
    host = host_for(configured)
    row = metadata(host.resource)
    if mutation == "owner":
        row["labels"] = []
    else:
        row[mutation] = "replacement"
    transport = SimpleNamespace(request=AsyncMock(return_value=row), execute=AsyncMock())
    provider = SpritesRuntimeProvider(transport=transport)
    with pytest.raises(WorkerDisconnected, match="ownership"):
        await provider.cleanup(host.resource)
    assert transport.request.await_count == 1
    transport.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_attachment_does_not_wake_a_sleeping_sprite(configured):
    host = host_for(configured)
    transport = SimpleNamespace(
        request=AsyncMock(return_value=metadata(host.resource, status="warm")), execute=AsyncMock()
    )
    with pytest.raises(WorkerDisconnected, match="lifecycle"):
        await SpritesRuntimeProvider(transport=transport).connect_task(host, host.resource, None)
    transport.execute.assert_not_awaited()


def test_connection_identity_includes_immutable_sprite_id(configured):
    resource = host_for(configured).resource
    connection = RuntimeConnection(resource_identity(resource))
    with pytest.raises(PermissionError):
        connection.validate({**resource, "sprites_id": "recreated"})


@pytest.mark.asyncio
async def test_pause_releases_hold_watch_and_confirms_platform_idle(configured):
    host = host_for(configured)
    calls, inspections = [], 0

    async def request(method, path, **kwargs):
        nonlocal inspections
        calls.append((method, path))
        if path.endswith("/services/puppyone-files"):
            return {"state": {"status": "running"}}
        if path.endswith("/exec"):
            return {"sessions": [{"id": "watch-session"}]}
        if path.endswith("/stop") or path.endswith("/kill"):
            return None
        inspections += 1
        return metadata(host.resource, status="running" if inspections == 1 else "warm")

    transport = SimpleNamespace(request=request, execute=AsyncMock(return_value=""))
    result = await SpritesRuntimeProvider(transport=transport).pause_resource(host)
    assert result.receipt(host.resource) == {"files_retained": True, "processes_retained": False}
    assert transport.execute.call_args.args[1][-2:] == ["release", host.artifact]
    assert any(path.endswith("watch-session/kill") for _, path in calls)
    assert all(method != "DELETE" for method, _ in calls)
    assert inspections == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("already_stopped", [True, False])
async def test_pause_retries_an_interrupted_service_stop(configured, already_stopped):
    host = host_for(configured)
    inspections, service_reads = 0, 0

    async def request(method, path, **kwargs):
        nonlocal inspections, service_reads
        if path.endswith("/services/puppyone-files"):
            service_reads += 1
            status = "stopped" if already_stopped or service_reads > 1 else "stopping"
            return {"state": {"status": status}}
        if path.endswith("/stop"):
            assert not already_stopped
            raise FileExistsError("already stopped")
        if path.endswith("/exec"):
            return {"sessions": []}
        inspections += 1
        return metadata(host.resource, status="running" if inspections == 1 else "cold")

    transport = SimpleNamespace(request=request, execute=AsyncMock(return_value=""))
    result = await SpritesRuntimeProvider(transport=transport).pause_resource(host)
    assert result.receipt(host.resource)["files_retained"] is True
    assert inspections == 2


@pytest.mark.asyncio
async def test_pause_failure_never_acknowledges_preservation(configured):
    host = host_for(configured)
    transport = SimpleNamespace(
        request=AsyncMock(return_value=metadata(host.resource)),
        execute=AsyncMock(side_effect=WorkerDisconnected("hold release failed")),
    )
    with pytest.raises(WorkerDisconnected):
        await SpritesRuntimeProvider(transport=transport).pause_resource(host)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error",
    [
        (401, WorkspaceUnavailable),
        (429, WorkspaceUnavailable),
        (503, WorkspaceUnavailable),
        (404, WorkerLost),
    ],
)
async def test_management_failures_do_not_leak_credentials_or_claim_absence(status, error):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="secret-provider-payload")
        )
    ) as client:
        transport = SpritesTransport("secret-provider-token", client=client)
        with pytest.raises(error) as failure:
            await transport.request("GET", "/v1/sprites/pw-fixture")
        assert "secret" not in str(failure.value)
        if status == 404:
            assert await transport.request("GET", "/v1/sprites/pw-fixture", missing=True) is None
        else:
            with pytest.raises(error):
                await transport.request("GET", "/v1/sprites/pw-fixture", missing=True)


@pytest.mark.asyncio
async def test_management_stream_cannot_buffer_unbounded_logs():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))
        )
    ) as client:
        with pytest.raises(WorkerDisconnected, match="exceeded limit"):
            await SpritesTransport("fixture", client=client).request(
                "POST", "/v1/sprites/pw-fixture/services/files/start", stream=True
            )


@pytest.mark.asyncio
async def test_real_websocket_streams_framed_files_through_unchanged_host(configured):
    row = configured
    row["resource"]["workbench_id"] = row["id"]
    seen, saved = [], {}

    async def server(socket):
        query = parse_qs(urlsplit(socket.request.path).query)
        assert query["cmd"] == [
            "sudo",
            "-n",
            "--",
            "node",
            "/opt/puppyone-agent/workbench-file-client.mjs",
        ]
        assert "env" not in query
        assert socket.request.headers["Authorization"] == "Bearer fixture-token"
        await socket.send(json.dumps({"type": "session_info", "session_id": "17", "tty": False}))
        buffer, sequence = b"", 0
        async for message in socket:
            if message == b"\x04":
                await socket.send(b"\x03\x00")
                return
            assert message[0] == 0
            buffer += message[1:]
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                frame = json.loads(line)
                seen.append(frame["action"])
                if frame["action"] == "hello":
                    result = {
                        **frame["value"],
                        "boot_id": str(uuid4()),
                        "service_id": str(uuid4()),
                        "runtime": {
                            "runtime_protocol": 1,
                            "file_protocol": 1,
                            "build_id": "a" * 64,
                            "capabilities": ["workspace_lifecycle", "native_files", "file_watch"],
                        },
                    }
                elif frame["value"]["action"] == "write":
                    saved[frame["value"]["path"]] = frame["value"]["content"]
                    result = {"saved": True}
                else:
                    result = {"content": saved[frame["value"]["path"]]}
                sequence += 1
                encoded = (
                    json.dumps(
                        {
                            "sequence": sequence,
                            "type": "control_result",
                            "id": frame["id"],
                            "result": result,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                ).encode()
                # Exercise UTF-8 split across WebSocket frames.
                for byte in encoded:
                    await socket.send(b"\x01" + bytes([byte]))

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]

        async def connector(url, **options):
            parsed = urlsplit(url)
            return await connect(f"ws://127.0.0.1:{port}{parsed.path}?{parsed.query}", **options)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
        ) as client:
            transport = SpritesTransport("fixture-token", client=client, connector=connector)
            host = WorkbenchChannel(row, purpose="files", store=InMemoryExecutionSessionStore())
            host.runtime = SpritesRuntimeProvider(transport=transport)
            try:
                await host.connect(RuntimeConnection(resource_identity(host.resource)))
                await host.control(
                    "files", {"action": "write", "path": "draft.md", "content": "实时修改"}
                )
                assert await host.control("files", {"action": "read", "path": "draft.md"}) == {
                    "content": "实时修改"
                }
                assert seen == ["hello", "files", "files"]
            finally:
                await host.close()


@pytest.mark.asyncio
async def test_exec_closed_without_exit_is_not_success():
    async def server(socket):
        await socket.send(json.dumps({"type": "session_info", "session_id": "18", "tty": False}))
        await socket.recv()
        await socket.close()

    async with serve(server, "127.0.0.1", 0) as listener:

        async def connector(url, **options):
            return await connect(
                f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}", **options
            )

        transport = SpritesTransport("fixture", connector=connector)
        transport.request = AsyncMock()
        with pytest.raises(WorkerDisconnected, match="exit"):
            await transport.execute("fixture", ["echo", "test"])
        assert transport.request.call_args.args[1].endswith("/exec/18/kill")
