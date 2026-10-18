"""docker sandbox lifecycle and full-duplex process transport."""

import asyncio
import json

from src.platform.scope_sandbox.execution.worker_port import WorkerDisconnected, WorkerLost
from .port import RuntimeCapabilities, read_process


class DockerRuntimeProvider:
    name = "docker"
    capabilities = RuntimeCapabilities(recovery_backend="docker-volume")

    def validate(self, artifact, resource=None):
        if not artifact:
            raise ValueError("A Docker worker image is required")

    async def allocate(self, host):
        inspected = json.loads(await host._docker("image", "inspect", host.image))[0]
        if host.restore_point and host.restore_point.get("artifact") != inspected["Id"]:
            raise ValueError("Recovery worker artifact mismatch")
        host.resource["image_id"] = inspected["Id"]
        mounts = [
            "--tmpfs=/workspace:rw,nosuid,nodev,size=256m,uid=1000,gid=1000",
            "--mount",
            "type=volume,source=" + host.resource["recovery_volume"] + ",target=/recovery",
        ]
        if host.resource.get("workbench_id"):
            generation = host.resource["workbench_generation"]
            mounts = [
                "--mount",
                "type=volume,source=puppyone-workbench-files-" + generation + ",target=/workspace",
                "--mount",
                "type=volume,source=puppyone-workbench-control-"
                + generation
                + ",target=/run/puppyone-workbench",
            ]
        await host._docker(
            "create",
            "--name",
            host.resource["resource_id"],
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--cap-add=SETUID",
            "--cap-add=SETGID",
            "--cap-add=KILL",
            "--cap-add=CHOWN",
            "--cap-add=DAC_OVERRIDE",
            "--cap-add=FOWNER",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=768m",
            "--cpus=1",
            "--user=0:0",
            *mounts,
            "--tmpfs=/tmp:rw,nosuid,nodev,size=128m,uid=1000,gid=1000",
            "--tmpfs=/run:rw,nosuid,nodev,size=128m,uid=0,gid=0,mode=0755",
            "--tmpfs=/home/node:rw,nosuid,nodev,size=8m,uid=1000,gid=1000",
            "-i",
            inspected["Id"],
        )
        await host._docker("start", host.resource["resource_id"])
        host.attached = True

    async def attach(self, host):
        try:
            state = json.loads(
                await host._docker("container", "inspect", host.resource["resource_id"])
            )[0]["State"]
        except RuntimeError as exc:
            if "No such container:" in str(exc) or "No such object:" in str(exc):
                raise WorkerLost("Sandbox no longer exists") from exc
            raise
        if not state["Running"]:
            raise WorkerLost("Sandbox tmpfs no longer exists")
        if state.get("Paused"):
            await host._docker("unpause", host.resource["resource_id"])

    async def resume(self, host):
        resource = host.resource
        try:
            found = json.loads(await host._docker("container", "inspect", resource["resource_id"]))[
                0
            ]
        except RuntimeError as exc:
            if "No such container" in str(exc) or "No such object" in str(exc):
                raise WorkerLost("Workspace was reclaimed") from exc
            raise
        artifact = json.loads(await host._docker("image", "inspect", host.image))[0]["Id"]
        if (
            not found["State"]["Running"]
            and resource.get("workbench_id")
            and found["Image"] == artifact
        ):
            await host._docker("start", resource["resource_id"])
            found["State"]["Running"] = True
        if found["Image"] != artifact or not found["State"]["Running"]:
            raise WorkerLost("Workspace artifact or filesystem unavailable")
        if found["State"].get("Paused"):
            await host._docker("unpause", resource["resource_id"])
        host.attached = True

    async def pause(self, host):
        if host.process:
            await host.process.wait()
        state = json.loads(
            await host._docker("container", "inspect", host.resource["resource_id"])
        )[0]["State"]
        if not state.get("Paused"):
            await host._docker("pause", host.resource["resource_id"])

    async def open_channel(self, host):
        command = (
            [
                "docker",
                "exec",
                "-i",
                "--user=0:0",
                host.resource["resource_id"],
                *host.attached_control_command(),
            ]
            if getattr(host, "attached", False)
            else ["docker", "start", "-ai", host.resource["resource_id"]]
        )
        host.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        read_process(host)

    async def snapshot(self, host):
        value = await host.control("snapshot")
        result = {
            "provider": "docker",
            "id": value["id"],
            "volume": host.resource["recovery_volume"],
            "project_id": host.project_id,
            "artifact": host.resource["image_id"],
        }
        return result

    async def send(self, host, data, *, timeout):
        host.process.stdin.write(data.encode())
        await host.process.stdin.drain()

    async def disconnect(self, host):
        return None

    async def renew(self, host):
        return None

    async def resolve(self, resource):
        if not resource.get("allocation_pending"):
            return [resource]
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "container",
            "inspect",
            resource["resource_id"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode:
            if b"No such" in stderr:
                return []
            raise WorkerDisconnected("Docker allocation lookup failed")
        found = json.loads(stdout)[0]
        return [{**resource, "image_id": found["Image"], "allocation_pending": False}]

    async def running_connection(self, host):
        await self.connect_task(host, host.resource, None)
        return None

    async def pause_resource(self, host):
        state = json.loads(
            await host._docker("container", "inspect", host.resource["resource_id"])
        )[0]["State"]
        if state["Running"] and not state.get("Paused"):
            await host._docker("pause", host.resource["resource_id"])

    async def cleanup(self, resource):
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            resource["resource_id"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode and b"No such container" not in stderr:
            raise RuntimeError("Pi sandbox cleanup failed")

    def artifact(self, image):
        return image

    def initialize(self, host):
        host.resource["recovery_volume"] = "puppyone-agent-recovery-" + host.execution_id

    def bind_recovery(self, host):
        host.resource["recovery_volume"] = host.restore_point["volume"]

    def restore_directory(self, recovery):
        return recovery["id"] if recovery else None

    async def connect_task(self, host, resource, connection):
        try:
            found = json.loads(await host._docker("container", "inspect", resource["resource_id"]))[
                0
            ]
        except RuntimeError as exc:
            if "No such container" in str(exc) or "No such object" in str(exc):
                raise WorkerLost("Workbench resource no longer exists") from exc
            raise
        if not found["State"]["Running"] or found["State"].get("Paused"):
            raise WorkerDisconnected("Workbench lifecycle must make the resource running")
        artifact = json.loads(await host._docker("image", "inspect", host.image))[0]["Id"]
        if found["Image"] != artifact:
            raise WorkerDisconnected("Workbench artifact differs from its binding")
