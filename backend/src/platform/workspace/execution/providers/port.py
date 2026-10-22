"""Provider boundary for sandbox lifecycle and a framed, full-duplex channel.

A provider owns SDK/process handles; it never owns a Run, authorizes a Project,
selects Git refs, or interprets Loop messages. The host carries channel state.
Provider credentials stay in this layer and never enter the sandbox environment.
"""

import asyncio
import codecs
import json
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

SEND_TIMEOUT = 15


@dataclass(frozen=True)
class RuntimeCapabilities:
    recovery_backend: str | None
    shared_workspace: bool = True
    native_files: bool = True
    file_retention: str = "provider-snapshot"
    preserves_processes: bool = True
    files_require_compute: bool = True


def resource_identity(resource: dict) -> str:
    # Task IDs and publication/control receipts are not compute identity.
    return json.dumps(
        {
            key: resource.get(key)
            for key in (
                "provider",
                "resource_id",
                "project_id",
                "workbench_id",
                "workbench_generation",
                "app",
                "image_id",
                "template",
                "volume_id",
            )
        },
        sort_keys=True,
    )


@dataclass(frozen=True)
class RuntimeConnection:
    """An inspected resource, never a sentinel asking the caller to reconnect."""

    identity: str
    handle: Any = None  # Opaque outside providers.

    def validate(self, resource: dict) -> None:
        if self.identity != resource_identity(resource):
            raise PermissionError("Provider connection resource binding mismatch")


@dataclass(frozen=True)
class RuntimeTransition:
    identity: str
    files_retained: bool
    processes_retained: bool

    def receipt(self, resource: dict) -> dict:
        if self.identity != resource_identity(resource) or not self.files_retained:
            raise RuntimeError("Workspace preservation was not confirmed")
        return {
            "files_retained": self.files_retained,
            "processes_retained": self.processes_retained,
        }


class RuntimeProvider(Protocol):
    name: str
    capabilities: RuntimeCapabilities

    def validate(self, artifact: str, resource: dict | None = None) -> None: ...
    def artifact(self, image: str) -> str: ...
    async def preflight(self, artifact: str) -> None: ...
    def initialize(self, host: Any) -> None: ...
    def bind_recovery(self, host: Any) -> None: ...
    def restore_directory(self, recovery: dict | None) -> str | None: ...
    async def allocate(self, host: Any) -> None: ...
    async def attach(self, host: Any) -> None: ...
    async def resume(self, host: Any) -> None: ...
    async def pause(self, host: Any) -> None: ...
    async def open_channel(self, host: Any) -> None: ...
    async def open_service(self, host: Any, service: str) -> None: ...
    async def send(self, host: Any, data: str, *, timeout: float) -> None: ...
    async def disconnect(self, host: Any) -> None: ...
    async def close_channel(self, host: Any) -> None: ...
    async def aclose(self) -> None: ...
    async def renew(self, host: Any) -> None: ...
    async def snapshot(self, host: Any) -> dict: ...
    async def resolve(self, resource: dict) -> list[dict]: ...
    async def cleanup(self, resource: dict) -> None: ...
    async def connect_task(
        self, host: Any, resource: dict, connection: RuntimeConnection | None
    ) -> None: ...
    async def running_connection(self, host: Any) -> RuntimeConnection: ...
    async def pause_resource(self, host: Any) -> RuntimeTransition: ...


async def close_process(host):
    """Close only a transport process; never mutate provider resources."""
    process = host.process
    if process:
        if process.stdin:
            process.stdin.close()
        try:
            async with asyncio.timeout(5):
                await process.wait()
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                async with asyncio.timeout(2):
                    await process.wait()
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()


def read_process(host):
    async def read():
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            while chunk := (await host.process.stdout.read(65536)):
                await host._output(decoder.decode(chunk))
            await host._output(decoder.decode(b"", final=True))
            await host._disconnected()
        except Exception as exc:
            await host.queue.put({"type": "failed", "error": type(exc).__name__})
            await host._disconnected()

    async def drain_stderr():
        while await host.process.stderr.read(65536):
            pass

    host.reader = asyncio.create_task(read())
    host.stderr_reader = asyncio.create_task(drain_stderr())
