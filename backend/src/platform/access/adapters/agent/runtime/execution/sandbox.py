"""Current task-control protocol. Allocation and deletion remain owner operations."""

from src.platform.access.adapters.agent.runtime.execution.port import ExecutionChannel


class SandboxExecution:
    async def quiesce(self, channel: ExecutionChannel, *, shared: bool = False) -> None:
        # These commands stop task writers; neither deletes the provider resource.
        await channel.control("quiesce" if shared else "stop")
