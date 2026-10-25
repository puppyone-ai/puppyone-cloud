"""Sprites resource ownership and lifecycle behind the existing RuntimeProvider.

No provider token or project credential is installed in the Sprite. Task holds
are finite and renewed by the existing lifecycle owner, never a detached loop.
"""

import asyncio
import hashlib
import json
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.sandbox.errors import WorkerDisconnected
from src.platform.sandbox.port import (
    RuntimeCapabilities,
    RuntimeConnection,
    RuntimeTransition,
    resource_identity,
)

from .bootstrap import invocation
from .transport import SpritesTransport

ARTIFACT = re.compile(r"sha256:[0-9a-f]{64}\Z")
NAME = re.compile(r"pw-[0-9a-f]{32}\Z")
SERVICE = "puppyone-files"
CONTROL = "/opt/puppyone-agent/sprites-control.py"


def load_bundle(artifact):
    path = Path(settings.CLOUD_AGENT_SPRITES_BUNDLE)
    if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("Sprites requires a worker bundle of at most 256 MiB")
    with path.open("rb") as stream:
        data = stream.read(256 * 1024 * 1024 + 1)
    if len(data) > 256 * 1024 * 1024:
        raise ValueError("Sprites worker bundle exceeds 256 MiB")
    if "sha256:" + hashlib.sha256(data).hexdigest() != artifact:
        raise ValueError("Sprites worker bundle digest mismatch")
    return data


class SpritesRuntimeProvider:
    name = "sprites"
    capabilities = RuntimeCapabilities(
        recovery_backend=None, file_retention="provider-filesystem", preserves_processes=False
    )

    def __init__(self, *, transport=None):
        self.transport = transport or SpritesTransport(settings.CLOUD_AGENT_SPRITES_TOKEN)

    def artifact(self, image):
        return "sha256:" + settings.CLOUD_AGENT_SPRITES_SHA256

    def validate(self, artifact, resource=None):
        if not settings.CLOUD_AGENT_SPRITES_TOKEN or not ARTIFACT.fullmatch(artifact or ""):
            raise ValueError("Sprites requires its token and pinned worker digest")
        if resource and resource.get("image_id") not in {None, artifact}:
            raise ValueError("Sprites artifact differs from the persisted binding")
        if not resource and not settings.CLOUD_AGENT_SPRITES_BUNDLE:
            raise ValueError("Sprites worker bundle is not configured")

    async def preflight(self, artifact):
        self.validate(artifact)
        await run_blocking(load_bundle, artifact)
        await self.transport.request("GET", "/v1/sprites", params={"max_results": 1})

    def initialize(self, host):
        host.resource["resource_id"] = "pw-" + UUID(host.execution_id).hex
        host.resource["image_id"] = host.artifact

    @staticmethod
    def _path(resource):
        name = resource.get("resource_id", "")
        if resource.get("provider") != "sprites" or not NAME.fullmatch(name):
            raise PermissionError("Invalid Sprites resource binding")
        return "/v1/sprites/" + name

    @staticmethod
    def _labels(resource):
        identity = {
            key: resource.get(key)
            for key in (
                "project_id",
                "workbench_id",
                "workbench_generation",
                "image_id",
                "resource_id",
            )
        }
        if any(value is None for value in identity.values()):
            raise ValueError("Sprites requires an owned workbench binding")
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
        return ["puppyone-workbench", "binding-" + digest]

    async def inspect(self, resource, *, missing=False):
        row = await self.transport.request("GET", self._path(resource), missing=missing)
        if row is None:
            return None
        if (
            row.get("name") != resource["resource_id"]
            or not row.get("id")
            or not set(self._labels(resource)).issubset(row.get("labels", []))
            or (resource.get("sprites_id") and row["id"] != resource["sprites_id"])
        ):
            raise WorkerDisconnected("Sprite ownership differs from its binding")
        return row

    async def _exec(self, resource, *command, **kwargs):
        self._path(resource)
        return await self.transport.execute(
            resource["resource_id"], ["sudo", "-n", "--", *command], **kwargs
        )

    async def _control(self, host, action):
        return await self._exec(host.resource, "python3", CONTROL, action, host.artifact)

    async def allocate(self, host):
        self.validate(host.artifact, host.resource)
        if host.restore_point:
            raise ValueError("Sprites workspaces cannot restore a legacy Agent snapshot")
        # Hash before allocating paid resources. Artifact is not the base OS image.
        bundle = await run_blocking(load_bundle, host.artifact)
        row = await self.inspect(host.resource, missing=True)
        if row is None:
            # A lost create response is reconciled by the same name and labels.
            with suppress(FileExistsError):
                await self.transport.request(
                    "POST",
                    "/v1/sprites",
                    json={
                        "name": host.resource["resource_id"],
                        "labels": self._labels(host.resource),
                        "url_settings": {"auth": "sprite"},
                    },
                )
            row = await self.inspect(host.resource)
        host.resource["sprites_id"] = row["id"]
        bootstrap = await run_blocking(Path(__file__).with_name("bootstrap.py").read_text)
        command, preamble = invocation(bootstrap, host.artifact)
        await self._exec(
            host.resource,
            *command,
            data=preamble + bundle,
            timeout=300,
        )
        # Defense in depth; VM-local UID rules additionally block direct IP egress.
        await self.transport.request(
            "POST",
            self._path(host.resource) + "/policy/network",
            json={"rules": [{"domain": "*", "action": "deny"}]},
        )
        await self.transport.request(
            "PUT",
            self._path(host.resource) + "/services/" + SERVICE,
            stream=True,
            params={"duration": "1s"},
            json={
                "cmd": "sudo",
                "args": ["-n", "--", "/usr/local/bin/node", "/opt/puppyone-agent/sprites-init.mjs"],
                "dir": "/",
                "env": {"HOME": "/home/node", "NODE_ENV": "production"},
            },
        )
        await self._start(host)
        host.resource["allocation_pending"] = False

    async def resume(self, host):
        if host.resource.get("allocation_pending"):
            await self.allocate(host)
            return
        await self._start(host)

    async def _start(self, host):
        self.validate(host.artifact, host.resource)
        await self.inspect(host.resource)
        service_path = self._path(host.resource) + "/services/" + SERVICE
        service = await self.transport.request("GET", service_path)
        if (service.get("state") or {}).get("status") not in {"running", "starting"}:
            await self.transport.request(
                "POST",
                service_path + "/start",
                stream=True,
                params={"duration": "1s"},
            )
        # This waits for the boot-specific readiness marker and renews a 5m hold.
        await self._control(host, "hold")
        host.attached = True

    async def attach(self, host):
        await self.connect_task(host, host.resource, None)

    async def running_connection(self, host):
        self.validate(host.artifact, host.resource)
        # Real metadata replicas can return an older cold/warm observation while
        # an exec and a task hold are still active. Retry read-only observations;
        # never issue exec to repair a genuinely sleeping workspace here.
        async with asyncio.timeout(10):
            for attempt in range(8):
                row = await self.inspect(host.resource)
                if row["status"] == "running":
                    break
                if attempt == 7:
                    raise WorkerDisconnected("Workbench lifecycle must wake the Sprite")
                await asyncio.sleep(0.25)
        await self._control(host, "ready")
        return RuntimeConnection(resource_identity(host.resource))

    async def prepare_connection(self, host):
        return await self.running_connection(host)

    async def connect_task(self, host, resource, connection):
        if resource.get("image_id") != host.artifact:
            raise WorkerDisconnected("Sprites worker artifact changed")
        host.resource = resource
        connection = connection or await self.running_connection(host)
        connection.validate(resource)
        host.connection, host.attached = connection, True

    async def open_channel(self, host):
        if host.connection is None:
            host.connection = await self.running_connection(host)
        host.connection.validate(host.resource)
        host.handle = await self.transport.open(
            host.resource["resource_id"], ["sudo", "-n", "--", *host.attached_control_command()]
        )
        channel = host.handle

        async def read():
            try:
                await channel.read(host._output)
            finally:
                await host._disconnected()

        host.reader = asyncio.create_task(read())

    async def open_service(self, host, service):
        if service != "files":
            raise ValueError("Unsupported Sprites workspace service")
        await self.open_channel(host)

    async def send(self, host, data, *, timeout):
        async with asyncio.timeout(timeout):
            await host.handle.send(data.encode())

    async def disconnect(self, host):
        if host.handle:
            channel, host.handle = host.handle, None
            await self.transport.finish(host.resource["resource_id"], channel)

    async def close_channel(self, host):
        await self.disconnect(host)
        host.connection = None
        await self.aclose()

    async def aclose(self):
        await self.transport.close()

    async def renew(self, host):
        # Reaper heartbeat; do not renew from the file watcher or status polling.
        await self._control(host, "hold")

    async def pause(self, host):
        await self.pause_resource(host)

    async def pause_resource(self, host):
        await self.inspect(host.resource)
        # A stale cold observation must not skip releasing a live hold or leave
        # the file service running. Pause owns lifecycle I/O and always performs
        # the release/stop protocol before trusting an idle observation.
        await self._control(host, "release")
        service_path = self._path(host.resource) + "/services/" + SERVICE
        service = await self.transport.request("GET", service_path)
        if (service.get("state") or {}).get("status") not in {"stopped", "failed"}:
            try:
                await self.transport.request(
                    "POST", service_path + "/stop", stream=True, params={"timeout": "5s"}
                )
            except FileExistsError:
                # A previous pause may have completed after its caller lost
                # the response. Only accept a conflict after observing stop.
                service = await self.transport.request("GET", service_path)
                if (service.get("state") or {}).get("status") not in {"stopped", "failed"}:
                    raise WorkerDisconnected("Sprites file service did not stop") from None
        # API processes may still have a watch bridge. These sessions belong
        # solely to this lifecycle-fenced VM; pause admission excludes occupants.
        sessions = await self.transport.request("GET", self._path(host.resource) + "/exec")
        for session in sessions.get("sessions", []):
            await self.transport.request(
                "POST",
                self._path(host.resource) + "/exec/" + quote(str(session["id"]), safe="") + "/kill",
                json={"signal": "SIGKILL", "timeout": 1},
                stream=True,
                missing=True,
            )
        # Metadata GET does not open a VM session. Never claim paused while
        # another hold/connection still keeps compute billable. A cold record
        # with no observed run is the stale creation snapshot, not a pause ACK.
        async with asyncio.timeout(120):
            while True:
                row = await self.inspect(host.resource)
                if row["status"] in {"warm", "cold"} and row.get("last_running_at"):
                    break
                await asyncio.sleep(1)
        return RuntimeTransition(resource_identity(host.resource), True, False)

    async def resolve(self, resource):
        row = await self.inspect(resource, missing=True)
        if row is None:
            return []
        # An allocated name does not prove bootstrap completed. Keep pending until
        # allocate finishes; a successor can safely retry the idempotent install.
        return [{**resource, "sprites_id": row["id"]}]

    async def cleanup(self, resource):
        if await self.inspect(resource, missing=True) is not None:
            await self.transport.request("DELETE", self._path(resource), missing=True)

    async def _identity_resource(self, resource_id, pool):
        if pool != "runtime":
            raise ValueError("Sprites has no legacy Scope Sandbox account")
        resource = {"provider": self.name, "resource_id": resource_id}
        row = await self.transport.request("GET", self._path(resource), missing=True)
        if row and (
            row.get("name") != resource_id or "puppyone-workbench" not in row.get("labels", [])
        ):
            raise PermissionError("Sprite is not an owned workspace")
        return resource, row

    async def destroy_resource(self, resource_id, *, pool="runtime"):
        resource, row = await self._identity_resource(resource_id, pool)
        if row is not None:
            await self.transport.request("DELETE", self._path(resource), missing=True)

    async def resource_absent(self, resource_id, *, pool="runtime"):
        _, row = await self._identity_resource(resource_id, pool)
        return row is None

    def bind_recovery(self, host):
        raise ValueError("Sprites does not support legacy Agent snapshot recovery")

    def restore_directory(self, recovery):
        if recovery:
            raise ValueError("Sprites does not restore legacy Agent snapshots")
        return None

    async def snapshot(self, host):
        raise ValueError("Sprites uses retained workspace files, not legacy Agent snapshots")
