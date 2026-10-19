"""Fly private Machines: durable workspace disk and SSH control transport.

Machines exec is not a bidirectional stream. flyctl SSH connects to one exact,
already-running Machine without a public service. Tokens are backend-only.
A Fly volume is not an E2B memory snapshot: legacy snapshot recovery is rejected.
"""

import asyncio
import base64
import os
import re
import shlex
import shutil
from urllib.parse import quote
from uuid import UUID

import httpx

from src.config import settings
from src.platform.scope_sandbox.execution.worker_port import WorkerDisconnected, WorkerLost

from .port import RuntimeCapabilities, read_process


class FlyRuntimeProvider:
    name = "fly"
    capabilities = RuntimeCapabilities(recovery_backend=None)

    def __init__(self, *, client=None):
        self.client = client

    def validate(self, artifact, resource=None):
        self._path(resource or {"app": settings.CLOUD_AGENT_FLY_APP})
        if not settings.CLOUD_AGENT_FLY_TOKEN or not shutil.which(settings.CLOUD_AGENT_FLY_CLI):
            raise ValueError("Fly requires its backend API token and flyctl SSH transport")
        if not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", artifact or ""):
            raise ValueError("Fly worker image must be pinned by digest")

    def artifact(self, image):
        return settings.CLOUD_AGENT_FLY_IMAGE

    def initialize(self, host):
        host.resource["app"] = settings.CLOUD_AGENT_FLY_APP
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

    async def _request(self, resource, method, suffix, **kwargs):
        if not settings.CLOUD_AGENT_FLY_TOKEN:
            raise ValueError("CLOUD_AGENT_FLY_TOKEN is required")
        options = {
            "headers": {"Authorization": f"Bearer {settings.CLOUD_AGENT_FLY_TOKEN}"},
            **kwargs,
        }
        path = self._path(resource, suffix)
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                if self.client is not None:
                    response = await self.client.request(method, path, **options)
                else:
                    async with httpx.AsyncClient(
                        base_url="https://api.machines.dev", timeout=60
                    ) as client:
                        response = await client.request(method, path, **options)
                break
            except httpx.TransportError:
                # Retrying a read cannot allocate a second Machine or obscure
                # an uncertain mutation. Allocation/start/stop keep reconciliation.
                if attempt + 1 == attempts:
                    raise
                await asyncio.sleep(0.2 * 2**attempt)
        response.raise_for_status()
        return response.json() if response.content else None

    def _machine(self, resource):
        return "/machines/" + quote(resource["resource_id"], safe="")

    @staticmethod
    def _metadata(resource):
        return {
            key: resource[key] for key in ("workbench_id", "workbench_generation", "project_id")
        }

    async def resolve(self, resource):
        if not resource.get("allocation_pending"):
            return [resource]
        metadata = self._metadata(resource)
        rows = await self._request(
            resource,
            "GET",
            "/machines",
            params={"metadata." + key: value for key, value in metadata.items()},
        )
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
        volume_name = (
            "pw_"
            + base64.b32encode(UUID(resource["workbench_generation"]).bytes)
            .decode()
            .rstrip("=")
            .lower()
        )
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
                    "size_gb": 1,
                    "encrypted": True,
                },
            )
        )
        if volume.get("attached_machine_id"):
            raise WorkerDisconnected("Fly workspace disk is already attached")
        resource["volume_id"] = volume["id"]
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

    async def inspect(self, resource):
        try:
            row = await self._request(resource, "GET", self._machine(resource))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise WorkerLost("Fly workbench Machine is unavailable; preserve its disk") from exc
            raise
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
        # Read only: attachment must never start or replace a resource.
        row = await self.inspect(resource)
        if row["state"] != "started":
            raise WorkerDisconnected("Workbench lifecycle must wake the Fly Machine")
        if row["config"]["image"] != host.artifact:
            raise PermissionError("Fly workbench artifact mismatch")

    async def open_channel(self, host):
        await self.attach(host)
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
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "FLY_API_TOKEN": settings.CLOUD_AGENT_FLY_TOKEN,
        }
        host.process = await asyncio.create_subprocess_exec(
            settings.CLOUD_AGENT_FLY_CLI,
            "ssh",
            "console",
            "--quiet",
            "--app",
            host.resource["app"],
            "--machine",
            host.resource["resource_id"],
            "--user",
            "root",
            "--command",
            shlex.join(command),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        read_process(host)

    async def send(self, host, data, *, timeout):
        host.process.stdin.write(data.encode())
        await host.process.stdin.drain()

    async def disconnect(self, host):
        if host.process and host.process.stdin:
            host.process.stdin.close()

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
        await self.attach(host)
        return None

    async def pause_resource(self, host):
        row = await self.inspect(host.resource)
        if row["state"] not in {"stopped", "suspended"}:
            await self.pause(host)
