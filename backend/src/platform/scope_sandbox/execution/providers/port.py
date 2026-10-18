"""Provider boundary for sandbox lifecycle and a framed, full-duplex channel.

A provider owns SDK/process handles; it never owns a Run, authorizes a Project,
selects Git refs, or interprets Loop messages. The host carries channel state.
Provider credentials stay in this layer and never enter the sandbox environment.
"""

import asyncio
import codecs

from dataclasses import dataclass
from typing import Any, Protocol

SEND_TIMEOUT = 15


@dataclass(frozen=True)
class RuntimeCapabilities:
    recovery_backend: str | None
    shared_workspace: bool = True
    native_files: bool = True


class RuntimeProvider(Protocol):
    name: str
    capabilities: RuntimeCapabilities

    def validate(self, artifact: str, resource: dict | None = None) -> None: ...
    def artifact(self, image: str) -> str: ...
    def initialize(self, host: Any) -> None: ...
    def bind_recovery(self, host: Any) -> None: ...
    def restore_directory(self, recovery: dict | None) -> str | None: ...
    async def allocate(self, host: Any) -> None: ...
    async def attach(self, host: Any) -> None: ...
    async def resume(self, host: Any) -> None: ...
    async def pause(self, host: Any) -> None: ...
    async def open_channel(self, host: Any) -> None: ...
    async def send(self, host: Any, data: str, *, timeout: float) -> None: ...
    async def disconnect(self, host: Any) -> None: ...
    async def renew(self, host: Any) -> None: ...
    async def snapshot(self, host: Any) -> dict: ...
    async def resolve(self, resource: dict) -> list[dict]: ...
    async def cleanup(self, resource: dict) -> None: ...
    async def connect_task(self, host: Any, resource: dict, connection: Any) -> None: ...
    async def running_connection(self, host: Any) -> Any: ...
    async def pause_resource(self, host: Any) -> None: ...


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
