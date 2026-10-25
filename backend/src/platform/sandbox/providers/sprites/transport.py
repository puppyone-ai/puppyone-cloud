"""Bounded async Sprites REST and non-TTY exec transport.

Wire contract: https://docs.fly.io/sprites/api/websockets/execute-command
Keep stdout streaming: retaining an entire Agent/file session grows without bound.
"""

import asyncio
import codecs
import json
from urllib.parse import quote, urlencode

import httpx
from websockets.asyncio.client import connect

from src.platform.sandbox.errors import WorkerDisconnected, WorkerLost, WorkspaceUnavailable

API = "https://api.sprites.dev"
CHUNK = 64 * 1024


class ExecChannel:
    def __init__(self, socket):
        self.socket = socket
        self.session_id = None
        self.exited = False

    async def send(self, data):
        for offset in range(0, len(data), CHUNK):
            await self.socket.send(b"\x00" + data[offset : offset + CHUNK])

    async def eof(self):
        await self.socket.send(b"\x04")

    async def read(self, output):
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            async for message in self.socket:
                if isinstance(message, str):
                    frame = json.loads(message)
                    if frame.get("type") == "session_info":
                        if frame.get("tty"):
                            raise WorkerDisconnected("Unexpected Sprites terminal mode")
                        self.session_id = str(frame["session_id"])
                    elif frame.get("type") == "exit":
                        self.exited = True
                        await output(decoder.decode(b"", final=True))
                        return int(frame["exit_code"])
                    elif frame.get("type") == "error":
                        raise WorkerDisconnected("Sprites exec failed")
                elif message:
                    stream, payload = message[0], message[1:]
                    if stream == 1:
                        await output(decoder.decode(payload))
                    elif stream == 3:
                        if len(payload) != 1:
                            raise WorkerDisconnected("Invalid Sprites exit frame")
                        self.exited = True
                        await output(decoder.decode(b"", final=True))
                        return payload[0]
                    elif stream != 2:
                        raise WorkerDisconnected("Invalid Sprites stream")
        except WorkerDisconnected:
            raise
        except Exception:
            # SDK/network errors can carry authorization headers or provider data.
            raise WorkerDisconnected("Sprites exec connection failed") from None
        raise WorkerDisconnected("Sprites connection closed without exit status")

    async def close(self):
        await self.socket.close()


class SpritesTransport:
    def __init__(self, token, *, client=None, connector=connect):
        self.token, self.client, self.connector = token, client, connector
        self.owns_client = client is None

    async def request(self, method, path, *, missing=False, stream=False, **kwargs):
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=30, trust_env=False)
        try:
            # Management streams are finite operations, not stdout sessions.
            # Bound total time and bytes even if a peer keeps sending log lines.
            async with (
                asyncio.timeout(60),
                self.client.stream(
                    method, API + path, headers={"Authorization": "Bearer " + self.token}, **kwargs
                ) as response,
            ):
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 4 * 1024 * 1024:
                        raise WorkerDisconnected("Sprites management response exceeded limit")
                    body.extend(chunk)
        except (httpx.RequestError, TimeoutError):
            raise WorkerDisconnected("Sprites management request failed") from None
        if response.status_code == 404:
            if missing:
                return None
            raise WorkerLost("Sprite resource no longer exists")
        if response.status_code in {401, 403}:
            raise WorkspaceUnavailable("workbench_provider_authentication")
        if response.status_code == 429 or response.status_code >= 500:
            raise WorkspaceUnavailable("workbench_connection_unavailable", retryable=True)
        if response.status_code == 409:
            raise FileExistsError("Sprites resource already exists")
        if not response.is_success:
            raise WorkerDisconnected(f"Sprites request rejected ({response.status_code})")
        try:
            if stream:
                for line in body.decode().splitlines():
                    if line.strip() and json.loads(line).get("type") == "error":
                        raise WorkerDisconnected("Sprites streamed operation failed")
                return None
            return json.loads(body) if body else None
        except (ValueError, UnicodeError, AttributeError):
            raise WorkerDisconnected("Invalid Sprites management response") from None

    async def open(self, name, command):
        params = [("cmd", value) for value in command]
        params += [("path", command[0]), ("stdin", "true"), ("dir", "/")]
        url = "wss://api.sprites.dev/v1/sprites/" + quote(name, safe="") + "/exec?"
        try:
            socket = await self.connector(
                url + urlencode(params),
                additional_headers={"Authorization": "Bearer " + self.token},
                proxy=None,
                open_timeout=30,
                close_timeout=1,
                ping_interval=15,
                ping_timeout=45,
                max_size=10 * 1024 * 1024,
            )
        except Exception:
            raise WorkerDisconnected("Sprites exec could not connect") from None
        return ExecChannel(socket)

    async def execute(self, name, command, *, data=b"", timeout=120):
        chunks, size = [], 0

        async def output(value):
            nonlocal size
            size += len(value.encode())
            if size > 1024 * 1024:
                raise WorkerDisconnected("Sprites command output exceeded limit")
            chunks.append(value)

        async with asyncio.timeout(timeout):
            channel = await self.open(name, command)
            reader = asyncio.create_task(channel.read(output))
            try:
                await channel.send(data)
                await channel.eof()
                if await reader:
                    raise WorkerDisconnected("Sprites runtime command failed")
                return "".join(chunks)
            finally:
                try:
                    await self.finish(name, channel)
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)

    async def finish(self, name, channel):
        await channel.close()
        # Disconnect alone leaves an exec running on Sprites. Terminate only this
        # transport process; never stop the shared file service or Agent task.
        if channel.session_id and not channel.exited:
            await self.request(
                "POST",
                "/v1/sprites/"
                + quote(name, safe="")
                + "/exec/"
                + quote(channel.session_id, safe="")
                + "/kill",
                json={"signal": "SIGKILL", "timeout": 1},
                stream=True,
                missing=True,
            )

    async def close(self):
        if self.owns_client and self.client is not None:
            await self.client.aclose()
            self.client = None
