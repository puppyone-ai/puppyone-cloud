"""e2b sandbox lifecycle and full-duplex process transport."""

import asyncio
import shlex
import time
from contextlib import suppress

from src.config import settings
from src.platform.sandbox.errors import WorkerDisconnected, WorkerLost, WorkspaceDiskLost
from src.platform.sandbox.port import (
    SEND_TIMEOUT,
    RuntimeCapabilities,
    RuntimeConnection,
    RuntimeTransition,
    resource_identity,
)


class E2BRuntimeProvider:
    name = "e2b"
    capabilities = RuntimeCapabilities(recovery_backend="provider-snapshot")

    def __init__(self):
        # A legacy session's snapshot and timeout update are incompatible E2B
        # mutations. Heartbeats must keep renewing the DB lease while the
        # provider snapshots, rather than block behind this lock or fail 409.
        self._lifecycle_lock = asyncio.Lock()

    def validate(self, artifact, resource=None):
        if not artifact or not settings.E2B_API_KEY:
            raise ValueError("E2B requires its API key and pinned worker template")

    async def preflight(self, artifact):
        from e2b import AsyncSandbox

        from src.platform.sandbox.errors import WorkspaceUnavailable

        self.validate(artifact)
        try:
            await AsyncSandbox.list(api_key=settings.E2B_API_KEY).next_items()
        except Exception as exc:
            # Credentials remain provider-owned; never expose SDK payloads.
            if type(exc).__name__ in {"AuthenticationException", "AuthorizationException"}:
                raise WorkspaceUnavailable("workbench_provider_authentication") from exc
            raise

    async def allocate(self, host):
        from e2b import AsyncSandbox

        if not host.artifact:
            raise RuntimeError("A pinned CLOUD_AGENT_E2B_TEMPLATE is required")
        if host.restore_point and host.restore_point.get("artifact") != host.artifact:
            raise ValueError("Recovery worker artifact mismatch")
        host.sandbox = await AsyncSandbox.create(
            template=host.restore_point["id"] if host.restore_point else host.artifact,
            timeout=settings.RUNTIME_AGENT_TIMEOUT_SECONDS,
            lifecycle={
                "on_timeout": {"action": "pause", "keep_memory": True},
                "auto_resume": False,
            },
            allow_internet_access=False,
            network={"allow_public_traffic": False},
            metadata={
                "agent_execution_id": host.execution_id,
                "project_id": host.project_id,
                **{
                    key: host.resource[key]
                    for key in ("workbench_id", "workbench_generation")
                    if host.resource.get(key)
                },
            },
            api_key=settings.E2B_API_KEY,
        )
        if host.restore_point:
            await host.sandbox.commands.run(
                "pkill -KILL -u 1000 || true; pkill -KILL -f '^node /opt/puppyone-agent/controller.mjs$' || true",
                user="root",
                timeout=30,
            )
        host.resource["resource_id"] = host.sandbox.sandbox_id
        host.resource["template"] = host.artifact

    async def attach(self, host):
        from e2b import AsyncSandbox
        from e2b.exceptions import NotFoundException

        resources = await self.resolve(host.resource)
        if len(resources) != 1:
            raise WorkerLost("No uniquely allocated sandbox workspace")
        host.resource = resources[0]
        try:
            host.sandbox = await AsyncSandbox.connect(
                host.resource["resource_id"], api_key=settings.E2B_API_KEY
            )
        except NotFoundException as exc:
            raise WorkerLost("Sandbox expired before unconfirmed files were saved") from exc

    async def resume(self, host):
        resource = host.resource
        from e2b import AsyncSandbox
        from e2b.exceptions import NotFoundException

        if resource.get("template") != host.artifact:
            raise WorkerLost("Workspace artifact changed")
        try:
            host.sandbox = await AsyncSandbox.connect(
                resource["resource_id"],
                timeout=settings.RUNTIME_AGENT_TIMEOUT_SECONDS,
                api_key=settings.E2B_API_KEY,
            )
        except NotFoundException as exc:
            raise WorkerLost("Workspace was reclaimed") from exc

    async def pause(self, host):
        async with self._lifecycle_lock:
            await host._disconnect_output()
            await host.sandbox.pause(keep_memory=True)

    async def open_service(self, host, service):
        if service != "files":
            raise ValueError(f"Unknown workspace service: {service}")
        # The template owns the supervisor. This command only bridges to its
        # socket; closing a backend connection cannot stop the file service.
        await self.open_channel(host)

    async def open_channel(self, host):
        host.handle = await host.sandbox.commands.run(
            shlex.join(host.control_command()),
            cwd="/workspace",
            user="root",
            stdin=True,
            background=True,
            timeout=0,
            on_stdout=host._output,
        )

        async def wait():
            try:
                await host.handle.wait()
            finally:
                await host._disconnected()

        host.reader = asyncio.create_task(wait())

    async def snapshot(self, host):
        async with self._lifecycle_lock:
            return await self._snapshot(host)

    async def _snapshot(self, host):
        await host.control("freeze")
        host.reconnecting = True
        try:
            await host._disconnect_output()
            async with asyncio.timeout(SEND_TIMEOUT):
                await host.sandbox.commands.list(request_timeout=SEND_TIMEOUT)
            snapshot = await host.sandbox.create_snapshot()
            result = {
                "provider": "e2b",
                "id": snapshot.snapshot_id,
                "project_id": host.project_id,
                "artifact": host.resource["template"],
            }
            host.buffer = ""
            host.handle = await host.sandbox.commands.connect(
                host.handle.pid, timeout=0, on_stdout=host._output
            )

            async def wait():
                try:
                    await host.handle.wait()
                finally:
                    await host._disconnected()

            host.reader = asyncio.create_task(wait())
        finally:
            host.reconnecting = False
            await host.control("thaw")
        return result

    async def send(self, host, data, *, timeout):
        await host.sandbox.commands.send_stdin(host.handle.pid, data, request_timeout=timeout)

    async def disconnect(self, host):
        if host.handle:
            async with asyncio.timeout(SEND_TIMEOUT):
                await host.handle.disconnect()

    async def close_channel(self, host):
        if host.handle:
            with suppress(Exception):
                async with asyncio.timeout(SEND_TIMEOUT):
                    await host.handle.kill()
        host.connection = None

    async def aclose(self):
        return None

    async def renew(self, host):
        if self._lifecycle_lock.locked() or host.parked:
            return
        async with self._lifecycle_lock:
            if host.sandbox and time.monotonic() >= host.timeout_renewal:
                await host.sandbox.set_timeout(settings.RUNTIME_AGENT_TIMEOUT_SECONDS)
                host.timeout_renewal = time.monotonic() + settings.RUNTIME_AGENT_TIMEOUT_SECONDS / 2

    async def resolve(self, resource):
        if resource["provider"] != "e2b" or not resource.get("allocation_pending"):
            return [resource]
        from e2b import AsyncSandbox
        from e2b.sandbox.sandbox_api import SandboxQuery

        matches = []
        pager = AsyncSandbox.list(
            query=SandboxQuery(
                metadata={
                    "agent_execution_id": resource["session_id"],
                    "project_id": resource["project_id"],
                }
            ),
            api_key=settings.E2B_API_KEY,
        )
        while pager.has_next:
            for sandbox in await pager.next_items():
                if (
                    sandbox.metadata.get("agent_execution_id") == resource["session_id"]
                    and sandbox.metadata.get("project_id") == resource["project_id"]
                ):
                    matches.append(
                        {**resource, "resource_id": sandbox.sandbox_id, "allocation_pending": False}
                    )
        return matches

    async def cleanup(self, resource):
        from e2b import AsyncSandbox
        from e2b.exceptions import NotFoundException

        for allocated in await self.resolve(resource):
            with suppress(NotFoundException):
                await AsyncSandbox.kill(allocated["resource_id"], api_key=settings.E2B_API_KEY)

    async def recreate(self, host):
        # No durable disk outlives this provider's compute.
        raise WorkspaceDiskLost("Provider compute is gone and holds the only copy")

    async def destroy_workspace(self, resource):
        await self.cleanup(resource)

    async def destroy_resource(self, resource_id, *, pool="runtime"):
        # One E2B API key owns every pool; a missing sandbox is already gone.
        await self.cleanup({"provider": "e2b", "resource_id": resource_id})

    async def resource_absent(self, resource_id, *, pool="runtime"):
        from e2b import AsyncSandbox
        from e2b.exceptions import NotFoundException

        # Only a definite not-found proves absence; any other failure must
        # surface so a deletion verification is retried, not assumed complete.
        try:
            await AsyncSandbox.get_info(resource_id, api_key=settings.E2B_API_KEY)
        except NotFoundException:
            return True
        return False

    def artifact(self, image):
        return settings.CLOUD_AGENT_E2B_TEMPLATE

    def initialize(self, host):
        host.resource["template"] = host.artifact

    def bind_recovery(self, host):
        return None

    def restore_directory(self, recovery):
        return None

    async def connect_task(self, host, resource, connection):
        # SDK connect may wake a paused VM. Tasks may only borrow a connection.
        if connection is None:
            raise PermissionError("An E2B connection from the workbench lifecycle is required")
        connection.validate(resource)
        if (
            not host.artifact
            or connection.handle.sandbox_id != resource["resource_id"]
            or resource.get("template") != host.artifact
        ):
            raise PermissionError("Shared workbench provider connection binding mismatch")
        host.connection, host.sandbox = connection, connection.handle

    async def running_connection(self, host):
        from e2b import AsyncSandbox
        from e2b.api import handle_api_exception
        from e2b.api.client.api.sandboxes import get_sandboxes_sandbox_id
        from e2b.api.client.models import SandboxDetail
        from e2b.api.client_async import get_api_client
        from e2b.connection_config import ConnectionConfig
        from packaging.version import Version

        # SDK connect() implicitly resumes and extends the timeout. Its GET
        # wrapper discards envd credentials, so use the pinned SDK's raw GET
        # and construct a transport without any lifecycle mutation. If pause
        # races this read, envd fails closed because auto_resume is disabled.
        config = ConnectionConfig(api_key=settings.E2B_API_KEY)
        response = await get_sandboxes_sandbox_id.asyncio_detailed(
            host.resource["resource_id"], client=get_api_client(config)
        )
        if response.status_code >= 300:
            raise handle_api_exception(response)
        info = response.parsed
        if not isinstance(info, SandboxDetail):
            raise WorkerDisconnected("E2B returned no sandbox connection details")
        if str(info.state) != "running":
            raise WorkerDisconnected("Workbench lifecycle must resume E2B")
        expected = {
            "agent_execution_id": host.resource["session_id"],
            "project_id": host.resource["project_id"],
            **{
                key: host.resource[key]
                for key in ("workbench_id", "workbench_generation")
                if host.resource.get(key)
            },
        }
        if (
            info.sandbox_id != host.resource["resource_id"]
            or not isinstance(info.metadata, dict)
            or any(info.metadata.get(key) != value for key, value in expected.items())
        ):
            raise PermissionError("E2B workspace binding mismatch")
        if getattr(info.lifecycle, "auto_resume", None) is not False:
            raise PermissionError("E2B workspace must disable automatic resume")
        token = info.envd_access_token if isinstance(info.envd_access_token, str) else None
        host.sandbox = AsyncSandbox(
            sandbox_id=info.sandbox_id,
            sandbox_domain=info.domain if isinstance(info.domain, str) else None,
            envd_version=Version(info.envd_version),
            envd_access_token=token,
            connection_config=ConnectionConfig(
                api_key=settings.E2B_API_KEY,
                # Keep the output subscription and pooled stdin RPCs on
                # separate HTTP/1.1 connections, measured faster for this
                # subscription/RPC workload while preserving one file channel.
                http_version="1.1",
                extra_sandbox_headers={
                    "E2b-Sandbox-Id": info.sandbox_id,
                    "E2b-Sandbox-Port": str(ConnectionConfig.envd_port),
                    **({"X-Access-Token": token} if token else {}),
                },
            ),
        )
        return RuntimeConnection(resource_identity(host.resource), host.sandbox)

    async def pause_resource(self, host):
        from e2b import AsyncSandbox

        info = await AsyncSandbox.get_info(
            host.resource["resource_id"], api_key=settings.E2B_API_KEY
        )
        if str(info.state) != "paused":
            await AsyncSandbox.pause(
                host.resource["resource_id"], keep_memory=True, api_key=settings.E2B_API_KEY
            )
        info = await AsyncSandbox.get_info(
            host.resource["resource_id"], api_key=settings.E2B_API_KEY
        )
        if str(info.state) != "paused":
            raise WorkerDisconnected("Sandbox pause was not confirmed")
        return RuntimeTransition(resource_identity(host.resource), True, True)
