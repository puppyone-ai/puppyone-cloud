"""e2b sandbox lifecycle and full-duplex process transport."""

import asyncio
import shlex
import time
from contextlib import suppress

from src.config import settings
from src.platform.scope_sandbox.execution.worker_port import WorkerDisconnected, WorkerLost

from .port import (
    SEND_TIMEOUT,
    RuntimeCapabilities,
    RuntimeConnection,
    RuntimeTransition,
    resource_identity,
)


class E2BRuntimeProvider:
    name = "e2b"
    # Legacy Pi sessions remain readable/recoverable; the supervised workspace
    # service has not been enabled for E2B. Reject new workbenches before allocation.
    capabilities = RuntimeCapabilities(recovery_backend="provider-snapshot", native_files=False)

    def validate(self, artifact, resource=None):
        if not artifact or not settings.E2B_API_KEY:
            raise ValueError("E2B requires its API key and pinned worker template")

    async def allocate(self, host):
        from e2b import AsyncSandbox

        if not host.artifact:
            raise RuntimeError("A pinned CLOUD_AGENT_E2B_TEMPLATE is required")
        if host.restore_point and host.restore_point.get("artifact") != host.artifact:
            raise ValueError("Recovery worker artifact mismatch")
        host.sandbox = await AsyncSandbox.create(
            template=host.restore_point["id"] if host.restore_point else host.artifact,
            timeout=settings.RUNTIME_AGENT_TIMEOUT_SECONDS,
            lifecycle={"on_timeout": "pause", "auto_resume": False},
            allow_internet_access=False,
            metadata={"agent_execution_id": host.execution_id, "project_id": host.project_id},
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
        await host._disconnect_output()
        await host.sandbox.pause()

    async def open_service(self, host, service):
        raise ValueError("E2B persistent workspace services are not enabled")

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

        info = await AsyncSandbox.get_info(
            host.resource["resource_id"], api_key=settings.E2B_API_KEY
        )
        if str(info.state) != "running":
            raise WorkerDisconnected("Workbench lifecycle must resume E2B")
        # Caller holds durable task occupancy or a fresh activity admission.
        host.sandbox = await AsyncSandbox.connect(
            host.resource["resource_id"], api_key=settings.E2B_API_KEY
        )
        return RuntimeConnection(resource_identity(host.resource), host.sandbox)

    async def pause_resource(self, host):
        from e2b import AsyncSandbox

        info = await AsyncSandbox.get_info(
            host.resource["resource_id"], api_key=settings.E2B_API_KEY
        )
        if str(info.state) != "paused":
            await AsyncSandbox.pause(host.resource["resource_id"], api_key=settings.E2B_API_KEY)
        info = await AsyncSandbox.get_info(
            host.resource["resource_id"], api_key=settings.E2B_API_KEY
        )
        if str(info.state) != "paused":
            raise WorkerDisconnected("Sandbox pause was not confirmed")
        return RuntimeTransition(resource_identity(host.resource), True, True)
