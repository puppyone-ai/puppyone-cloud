"""Fly private Machines: durable workspace disk and SSH control transport.

Machines exec is not a bidirectional stream. flyctl SSH connects to one exact,
already-running Machine without a public service. Tokens are backend-only.
A Fly volume is not an E2B memory snapshot: legacy snapshot recovery is rejected.
"""

import asyncio
import base64
import re
import shlex
import shutil
from urllib.parse import quote
from uuid import UUID

import httpx

from src.config import settings
from src.platform.sandbox.errors import (
    WorkerDisconnected,
    WorkerLost,
    WorkspaceDiskLost,
    WorkspaceUnavailable,
)
from src.platform.sandbox.port import (
    RuntimeCapabilities,
    RuntimeConnection,
    RuntimeTransition,
    close_process,
    read_process,
    resource_identity,
)
from src.platform.sandbox.timing import workspace_stage

from .naming import dedicated, workspace_app
from .transport import acquire_transport, close_transports

# A deleted app frees its name asynchronously, and two owners can race to create it.
APP_CREATE_ATTEMPTS = 15
APP_CREATE_RETRY_SECONDS = 2


def _transport_app(resource):
    # One agent carries a tunnel per private network, so dedicated apps share it.
    return settings.CLOUD_AGENT_FLY_APP if dedicated(resource) else resource["app"]


class FlyRuntimeProvider:
    name = "fly"
    capabilities = RuntimeCapabilities(
        recovery_backend=None,
        file_retention="volume",
        preserves_processes=False,
        connection_preparation=True,
    )

    def __init__(self, *, client=None):
        self.client = client
        self._owned_client = None
        self._transport = None
        self._ssh_lease = None

    def validate(self, artifact, resource=None):
        self._path(resource or {"app": settings.CLOUD_AGENT_FLY_APP})
        if not settings.CLOUD_AGENT_FLY_TOKEN or not shutil.which(settings.CLOUD_AGENT_FLY_CLI):
            raise ValueError("Fly requires its backend API token and flyctl SSH transport")
        if not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", artifact or ""):
            raise ValueError("Fly worker image must be pinned by digest")

    async def preflight(self, artifact):
        self.validate(artifact)
        await self._request({"app": settings.CLOUD_AGENT_FLY_APP}, "GET", "")

    def artifact(self, image):
        return settings.CLOUD_AGENT_FLY_IMAGE

    def initialize(self, host):
        workspace = getattr(host, "dedicated_workspace", None)
        host.resource["app"] = (
            workspace_app(workspace)
            if workspace and settings.CLOUD_AGENT_FLY_ORG
            else settings.CLOUD_AGENT_FLY_APP
        )
        host.resource["image_id"] = host.artifact

    def bind_recovery(self, host):
        raise ValueError("Fly volumes cannot restore Session snapshot checkpoints")

    def restore_directory(self, recovery):
        if recovery:
            raise ValueError("Fly volumes cannot restore Session snapshot checkpoints")
        return None

    def _path(self, resource, suffix=""):
        app = resource.get("app")
        if not isinstance(app, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", app):
            raise ValueError("A persisted Fly app binding is required")
        return f"/v1/apps/{app}{suffix}"

    async def _request(self, resource, method, suffix, *, token=None, **kwargs):
        return await self._send(method, self._path(resource, suffix), token=token, **kwargs)

    async def _send(self, method, path, *, token=None, **kwargs):
        # ``token`` selects another provider account; it is never persisted.
        if token is None:
            if not settings.CLOUD_AGENT_FLY_TOKEN:
                raise ValueError("CLOUD_AGENT_FLY_TOKEN is required")
            token = settings.CLOUD_AGENT_FLY_TOKEN
        elif not token:
            raise ValueError("A Fly API token is required for this resource pool")
        options = {
            "headers": {"Authorization": f"Bearer {token}"},
            **kwargs,
        }
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                if self.client is not None:
                    response = await self.client.request(method, path, **options)
                else:
                    if self._owned_client is None:
                        self._owned_client = httpx.AsyncClient(
                            base_url="https://api.machines.dev",
                            timeout=60,
                            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
                        )
                    response = await self._owned_client.request(method, path, **options)
                break
            except httpx.TransportError:
                # Retrying a read cannot allocate a second Machine or obscure
                # an uncertain mutation. Allocation/start/stop keep reconciliation.
                if attempt + 1 == attempts:
                    raise
                await asyncio.sleep(0.2 * 2**attempt)
        if response.status_code in {401, 403}:
            raise WorkspaceUnavailable("workbench_provider_authentication")
        if response.status_code == 422 and "machine limit" in response.text.lower():
            # An organization quota is an operator action, not a transient fault.
            raise WorkspaceUnavailable("workbench_provider_capacity", retryable=True)
        response.raise_for_status()
        return response.json() if response.content else None

    def _machine(self, resource):
        return "/machines/" + quote(resource["resource_id"], safe="")

    @staticmethod
    def _volume_name(generation):
        # Deterministic per generation, so a lost create reply is recovered by name.
        return "pw_" + base64.b32encode(UUID(generation).bytes).decode().rstrip("=").lower()

    async def _delete(self, resource, suffix, **kwargs):
        try:
            await self._request(resource, "DELETE", suffix, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:  # already gone
                raise

    async def _ensure_app(self, resource):
        if not dedicated(resource):
            return
        app = resource["app"]
        for _ in range(APP_CREATE_ATTEMPTS):
            try:
                row = await self._request(resource, "GET", "")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
            else:
                if row.get("network") != app:
                    raise PermissionError("Fly Workspace app is on another private network")
                return
            try:
                await self._send(
                    "POST",
                    "/v1/apps",
                    json={
                        "app_name": app,
                        "org_slug": settings.CLOUD_AGENT_FLY_ORG,
                        "network": app,
                    },
                )
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {409, 422}:
                    raise
            await asyncio.sleep(APP_CREATE_RETRY_SECONDS)
        raise WorkerDisconnected("Fly Workspace app is not available yet; retry")

    async def _create_machine(self, host, volume):
        resource = host.resource
        # Only a machine alone on its private network may open the internet.
        internet = getattr(host, "internet_access", False) and dedicated(resource)
        machine = await self._request(
            resource,
            "POST",
            "/machines",
            json={
                "name": "pw-" + resource["workbench_generation"],
                "region": volume["region"],
                "config": {
                    "image": host.artifact,
                    "metadata": self._metadata(resource),
                    "guest": {"cpu_kind": "shared", "cpus": 1, "memory_mb": 1024},
                    "mounts": [{"volume": volume["id"], "path": "/data"}],
                    "services": [],
                    "auto_destroy": False,
                    "restart": {"policy": "no"},
                    "init": {"exec": ["node", "/opt/puppyone-agent/fly-init.mjs"]},
                    "env": {"PUPPYONE_TASK_INTERNET": "open"} if internet else {},
                },
            },
        )
        resource.update(resource_id=machine["id"], allocation_pending=False, image_id=host.artifact)
        await self._request(
            resource,
            "GET",
            self._machine(resource) + "/wait",
            params={"state": "started", "timeout": 60},
        )
        host.attached = True

    @staticmethod
    def _pool_account(pool):
        if pool == "runtime":
            return settings.CLOUD_AGENT_FLY_APP, settings.CLOUD_AGENT_FLY_TOKEN
        if pool == "legacy_scope":
            # Retired Scope Sandbox machines live in their own Fly app.
            return settings.SCOPE_SANDBOX_FLY_APP, settings.SCOPE_SANDBOX_FLY_TOKEN
        raise ValueError("Unknown sandbox resource pool")

    async def destroy_resource(self, resource_id, *, pool="runtime"):
        app, token = self._pool_account(pool)
        resource = {"app": app, "resource_id": resource_id}
        try:
            await self._request(
                resource, "DELETE", self._machine(resource), token=token,
                params={"force": "true"},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:  # already gone
                raise

    async def resource_absent(self, resource_id, *, pool="runtime"):
        app, token = self._pool_account(pool)
        resource = {"app": app, "resource_id": resource_id}
        try:
            row = await self._request(resource, "GET", self._machine(resource), token=token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return True
            raise
        return (row or {}).get("state") == "destroyed"

    @staticmethod
    def _metadata(resource):
        return {
            key: resource[key] for key in ("workbench_id", "workbench_generation", "project_id")
        }

    async def resolve(self, resource):
        if not resource.get("allocation_pending"):
            return [resource]
        metadata = self._metadata(resource)
        try:
            rows = await self._request(
                resource,
                "GET",
                "/machines",
                params={"metadata." + key: value for key, value in metadata.items()},
            )
        except httpx.HTTPStatusError as exc:
            # The intent is persisted first, so a crash can precede the app.
            if exc.response.status_code == 404 and dedicated(resource):
                return []
            raise
        matching = [
            row
            for row in rows
            if all(
                row.get("config", {}).get("metadata", {}).get(k) == v for k, v in metadata.items()
            )
        ]
        if any(row.get("config", {}).get("image") != resource.get("image_id") for row in matching):
            raise PermissionError("Fly allocation artifact differs from its persisted intent")
        return [
            {
                **resource,
                "resource_id": row["id"],
                "allocation_pending": False,
                "image_id": row["config"]["image"],
                "volume_id": row["config"]["mounts"][0]["volume"],
            }
            for row in matching
            if all(
                row.get("config", {}).get("metadata", {}).get(k) == v for k, v in metadata.items()
            )
        ]

    async def allocate(self, host):
        resource = host.resource
        if not resource.get("workbench_id") or host.restore_point:
            raise ValueError("Fly requires an independently owned persistent workbench")
        if not host.artifact or "@sha256:" not in host.artifact:
            raise ValueError("CLOUD_AGENT_FLY_IMAGE must be pinned by digest")
        await self._ensure_app(resource)
        existing = await self.resolve(resource)
        if len(existing) > 1:
            raise WorkerDisconnected(
                "Ambiguous Fly allocation; retain resources for reconciliation"
            )
        if existing:
            host.resource = existing[0]
            await self.resume(host)
            return
        # The operation owner persists this deterministic intent BEFORE calling
        # the provider. Lost create replies are recovered by exact generation.
        volume_name = self._volume_name(resource["workbench_generation"])
        volumes = await self._request(resource, "GET", "/volumes")
        matches = [v for v in volumes if v.get("name") == volume_name]
        if len(matches) > 1:
            raise WorkerDisconnected("Ambiguous Fly disk allocation; no automatic deletion")
        volume = (
            matches[0]
            if matches
            else await self._request(
                resource,
                "POST",
                "/volumes",
                json={
                    "name": volume_name,
                    "region": settings.CLOUD_AGENT_FLY_REGION,
                    "size_gb": settings.CLOUD_AGENT_FLY_DISK_GB,
                    "encrypted": True,
                },
            )
        )
        if volume.get("attached_machine_id"):
            raise WorkerDisconnected("Fly workspace disk is already attached")
        resource["volume_id"] = volume["id"]
        await self._create_machine(host, volume)

    async def recreate(self, host):
        """Start new compute on the surviving disk of ``host.resource``.

        The caller persisted the intent (``allocation_pending``) first, so a lost
        create reply is adopted by exact generation metadata, never duplicated.
        """
        resource = host.resource
        if not resource.get("allocation_pending"):
            raise ValueError("Recreating compute requires a persisted allocation intent")
        existing = await self.resolve(resource)
        if len(existing) > 1:
            raise WorkerDisconnected("Ambiguous Fly recreation; retain resources for reconciliation")
        if existing:
            if existing[0]["volume_id"] != resource.get("volume_id"):
                raise PermissionError("Recreated Fly compute is bound to another disk")
            host.resource = existing[0]
            await self.resume(host)
            return
        volume = await self._require_disk(resource)
        if volume.get("attached_machine_id"):
            raise WorkerDisconnected("Fly workspace disk is still attached; retry")
        await self._create_machine(host, volume)

    async def destroy_workspace(self, resource):
        """Destroy all compute and the disk of one generation whose owner was deleted."""
        if dedicated(resource):
            # A rebuild recreates the app; keeping it empty would strand it.
            await self._delete(resource, "", params={"force": "true"})
            return
        metadata = self._metadata(resource)
        rows = await self._request(
            resource,
            "GET",
            "/machines",
            params={"metadata." + key: value for key, value in metadata.items()},
        )
        machines = {
            row["id"]
            for row in rows
            if all(row.get("config", {}).get("metadata", {}).get(k) == v for k, v in metadata.items())
        }
        if resource.get("resource_id"):
            machines.add(resource["resource_id"])
        for machine in sorted(machines):
            await self._delete(
                resource, "/machines/" + quote(machine, safe=""), params={"force": "true"}
            )
        name = self._volume_name(resource["workbench_generation"])
        for volume in await self._request(resource, "GET", "/volumes"):
            if volume.get("id") != resource.get("volume_id") and volume.get("name") != name:
                continue
            if volume.get("state") in {"destroyed", "destroying", "pending_destroy"}:
                continue
            try:
                await self._delete(resource, "/volumes/" + quote(volume["id"], safe=""))
            except httpx.HTTPStatusError as exc:
                # A force-destroyed Machine releases its disk asynchronously.
                raise WorkerDisconnected("Fly workspace disk is not released yet; retry") from exc

    async def inspect(self, resource):
        try:
            row = await self._request(resource, "GET", self._machine(resource))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise WorkerLost("Fly workbench Machine is unavailable; preserve its disk") from exc
            raise
        if row.get("state") in {"destroyed", "destroying"}:
            # Fly keeps returning recently destroyed Machines; they never start again.
            raise WorkerLost("Fly workbench Machine was destroyed; preserve its disk")
        if any(
            row.get("config", {}).get("metadata", {}).get(k) != v
            for k, v in self._metadata(resource).items()
        ):
            raise PermissionError("Fly workbench generation mismatch")
        if resource.get("image_id") and row.get("config", {}).get("image") != resource["image_id"]:
            raise PermissionError("Fly workbench artifact mismatch")
        if resource.get("volume_id"):
            # Fly enriches mounts with name, encryption and size. Identity and
            # mount location must match; provider response metadata is not ours.
            mounts = row.get("config", {}).get("mounts") or []
            if len(mounts) != 1 or (mounts[0].get("volume"), mounts[0].get("path")) != (
                resource["volume_id"],
                "/data",
            ):
                raise PermissionError("Fly workbench disk binding mismatch")
        return row

    async def attach(self, host):
        row = await self.inspect(host.resource)
        if row["state"] != "started":
            raise WorkerDisconnected("Workbench lifecycle must wake the Fly Machine")
        host.attached = True
        return row

    async def resume(self, host):
        row = await self.inspect(host.resource)
        if row["state"] in {"stopped", "suspended"}:
            await self._request(host.resource, "POST", self._machine(host.resource) + "/start")
        elif row["state"] != "started":
            raise WorkerDisconnected("Fly workbench lifecycle transition is not complete")
        await self._request(
            host.resource,
            "GET",
            self._machine(host.resource) + "/wait",
            params={"state": "started", "timeout": 60},
        )
        host.attached = True

    async def pause(self, host):
        await self.inspect(host.resource)
        await self._request(host.resource, "POST", self._machine(host.resource) + "/stop")
        await self._request(
            host.resource,
            "GET",
            self._machine(host.resource) + "/wait",
            params={"state": "stopped", "timeout": 60},
        )

    async def connect_task(self, host, resource, connection):
        if resource.get("image_id") != host.artifact:
            raise PermissionError("Fly workbench artifact mismatch")
        # A lifecycle-admitted handle avoids repeated management API lookups.
        if connection is not None:
            connection.validate(resource)
            host.connection = connection
            return
        # Read only: attachment must never start or replace a resource.
        row = await self.inspect(resource)
        if row["state"] != "started":
            raise WorkerDisconnected("Workbench lifecycle must wake the Fly Machine")
        if row["config"]["image"] != host.artifact:
            raise PermissionError("Fly workbench artifact mismatch")
        host.connection = RuntimeConnection(resource_identity(resource), row.get("private_ip"))

    async def open_channel(self, host):
        if getattr(host, "connection", None) is None:
            host.connection = await self.running_connection(host)
        host.connection.validate(host.resource)
        connection = host.connection
        # Fly's started event is earlier than our firewall/mount initialization.
        # Only this boot's root-written receipt admits a control process.
        command = [
            "sh",
            "-c",
            "boot=$(cat /proc/sys/kernel/random/boot_id); i=0; "
            'until [ "$(cat /run/puppyone-fly-ready 2>/dev/null)" = "$boot" ]; do '
            'i=$((i+1)); [ "$i" -lt 150 ] || exit 1; sleep 0.2; done; exec "$@"',
            "fly-control",
            *host.control_command(),
        ]
        if self._transport is not None:
            await self.close_channel(host)
            host.connection = connection
        with workspace_stage("connection.agent_acquire"):
            self._transport = await acquire_transport(
                _transport_app(host.resource),
                settings.CLOUD_AGENT_FLY_TOKEN,
                settings.CLOUD_AGENT_FLY_CLI,
            )
        try:
            self._ssh_lease = await self._transport.ssh_connections().open(
                host.resource,
                host.connection.handle,
                shlex.join(command),
                epoch=getattr(host, "compute_epoch", None),
            )
            host.process = self._ssh_lease.process
        except BaseException:
            self._transport.release()
            self._transport = None
            raise
        read_process(host)

    async def open_service(self, host, service):
        if service != "files":
            raise ValueError("Unsupported workspace service")
        await self.open_channel(host)

    async def send(self, host, data, *, timeout):
        host.process.stdin.write(data.encode())
        await host.process.stdin.drain()

    async def disconnect(self, host):
        if host.process and host.process.stdin:
            host.process.stdin.close()

    async def close_channel(self, host):
        try:
            await close_process(host)
        finally:
            if self._ssh_lease is not None:
                self._ssh_lease.release()
                self._ssh_lease = None
            host.connection = None
            await self.aclose()
            if self._transport is not None:
                self._transport.release()
                self._transport = None

    shutdown_transports = staticmethod(close_transports)

    async def aclose(self):
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

    async def renew(self, host):
        return None  # Fly compute lifetime is controlled by our activity leases.

    async def snapshot(self, host):
        raise ValueError("Fly volume preservation is owned by the workspace lifecycle")

    async def cleanup(self, resource):
        # Cleanup only accepts an identity durably marked safe by its owner.
        # Disk deletion is deliberately a separate, explicit preservation decision.
        matches = await self.resolve(resource)
        if resource.get("allocation_pending") and len(matches) != 1:
            raise WorkerDisconnected("Ambiguous Fly cleanup; retain the original allocation intent")
        for resolved in matches:
            try:
                await self.inspect(resolved)
                await self._request(
                    resolved, "DELETE", self._machine(resolved), params={"force": "true"}
                )
            except WorkerLost:
                pass

    async def running_connection(self, host):
        row = await self.attach(host)
        return RuntimeConnection(resource_identity(host.resource), row.get("private_ip"))

    async def prepare_connection(self, host):
        """Prepare our private route while the lifecycle owner starts compute.

        This handle proves identity, not readiness. The consumer must still wait
        for the durable checking_files phase and verify the current boot/epoch.
        """
        with workspace_stage("connection.prepare_inspect"):
            row = await self.inspect(host.resource)
        if row["state"] not in {"stopped", "suspended", "starting", "started"}:
            raise WorkerDisconnected("Fly workbench transition is not ready for preparation")
        transport = await acquire_transport(
            _transport_app(host.resource),
            settings.CLOUD_AGENT_FLY_TOKEN,
            settings.CLOUD_AGENT_FLY_CLI,
        )
        try:
            await transport.ssh_connections().prepare(host.resource, row.get("private_ip"))
            return RuntimeConnection(resource_identity(host.resource), row.get("private_ip"))
        finally:
            transport.release()

    async def pause_resource(self, host):
        try:
            try:
                row = await self.inspect(host.resource)
            except WorkerLost:
                # Compute is already gone, which is what pausing asks for. The
                # disk must survive; resume recreates compute on it.
                await self._require_disk(host.resource)
                return RuntimeTransition(resource_identity(host.resource), True, False)
            if row["state"] not in {"stopped", "suspended"}:
                await self.pause(host)
            return RuntimeTransition(resource_identity(host.resource), True, False)
        finally:
            await self.aclose()

    async def _require_disk(self, resource):
        if not resource.get("volume_id"):
            raise WorkspaceDiskLost("Fly workspace has no recorded disk")
        try:
            volume = await self._request(
                resource, "GET", "/volumes/" + quote(resource["volume_id"], safe="")
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise WorkspaceDiskLost("Fly workspace disk is gone") from exc
            raise
        if volume.get("state") in {"destroyed", "destroying", "pending_destroy"}:
            raise WorkspaceDiskLost("Fly workspace disk is gone")
        return volume
