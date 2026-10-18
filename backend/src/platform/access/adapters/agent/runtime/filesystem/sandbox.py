"""Keep the existing native working-directory protocol and recovery material."""

from src.platform.access.adapters.agent.runtime.filesystem.port import (
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

    async def push(self, channel: FilesystemChannel, candidate: str) -> Row:
        return await channel.control("push", {"candidate": candidate})

    async def operation(self, channel: FilesystemChannel, operation: Row) -> Row:
        response = await channel.control("files", operation)
        error = response.get("error")
        if error:
            raise FilesystemError(error)
        return response["result"]

    async def watch(self, channel: FilesystemChannel) -> None:
        await channel.control("watch")
