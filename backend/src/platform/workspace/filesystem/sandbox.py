"""Keep the existing native working-directory protocol and recovery material."""

import base64
import binascii
import zlib
from asyncio import Queue
from contextlib import AbstractAsyncContextManager

from src.platform.workspace.filesystem.port import (
    FileServiceChannel,
    FilesystemChannel,
    FilesystemError,
    Row,
)


class SandboxFilesystem:
    def bind_recovery(self, channel: FilesystemChannel, recovery: Row | None) -> None:
        channel.restore_point = recovery

    async def prepare(
        self, channel: FilesystemChannel, workspace: Row, *, resume: bool
    ) -> Row | None:
        return await channel.prepare({"workspace": workspace, "resume_workspace": resume})

    async def inspect(self, channel: FilesystemChannel, workspace: Row) -> Row:
        return await channel.control("inspect", workspace)

    async def snapshot(self, channel: FilesystemChannel) -> Row:
        return await channel.snapshot()

    async def finalize(self, channel: FilesystemChannel, message: str) -> Row:
        return await channel.control("finalize", {"message": message})

    async def push(self, channel: FilesystemChannel, candidate: str, *, remote_tip=None) -> Row:
        value = {"candidate": candidate}
        if remote_tip:
            value["remote_tip"] = remote_tip
        return await channel.control("push", value)

    async def operation(self, channel: FileServiceChannel, operation: Row) -> Row:
        request = (
            {**operation, "transfer_encoding": "deflate"}
            if operation["action"] == "read"
            else operation
        )
        # Negotiate before encoding writes: an older worker must never mistake
        # compressed bytes for native file content. Reads already negotiate in
        # the request; writes need the service's explicit capability receipt.
        if operation["action"] == "write" and "deflate" in getattr(
            channel, "service_receipt", {}
        ).get("write_encodings", []):
            try:
                raw = base64.b64decode(operation["content_base64"], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise FilesystemError("file_operation_rejected") from exc
            if len(raw) > 8 * 1024 * 1024:
                raise FilesystemError("file_operation_rejected")
            packed = zlib.compress(raw, level=1)
            if len(packed) + 64 < len(raw):
                request = {
                    **operation,
                    "content_base64": base64.b64encode(packed).decode(),
                    "content_encoding": "deflate",
                }
        response = await channel.control("files", request)
        error = response.get("error")
        if error:
            raise FilesystemError(error)
        result = dict(response["result"])
        encoding = result.pop("content_encoding", None)
        if encoding is not None:
            try:
                if (
                    encoding != "deflate"
                    or operation["action"] != "read"
                    or len(result["content_base64"]) > 12 * 1024 * 1024
                ):
                    raise ValueError("Unsupported file transfer")
                decoder = zlib.decompressobj()
                data = decoder.decompress(
                    base64.b64decode(result["content_base64"], validate=True), 8 * 1024 * 1024 + 1
                )
                if (
                    len(data) > 8 * 1024 * 1024
                    or not decoder.eof
                    or decoder.unconsumed_tail
                    or decoder.unused_data
                ):
                    raise ValueError("Invalid file transfer size or framing")
                result["content_base64"] = base64.b64encode(data).decode()
            except (ValueError, KeyError, binascii.Error, zlib.error) as exc:
                raise FilesystemError("file_operation_rejected") from exc
        return result

    async def watch(self, channel: FileServiceChannel) -> None:
        await channel.control("watch")

    def subscribe(self, channel: FileServiceChannel) -> AbstractAsyncContextManager[Queue[Row]]:
        return channel.subscribe()
