"""Task execution boundaries, independent of whole-resource lifecycle."""

from typing import Any, Protocol

Row = dict[str, Any]


class ExecutionChannel(Protocol):
    async def control(self, action: str, value: Row | None = None) -> Row: ...


class ExecutionPort(Protocol):
    async def quiesce(self, channel: ExecutionChannel, *, shared: bool = False) -> None: ...


class BoundToolPort(Protocol):
    """Trusted tools run in the backend; generated code uses isolated execution."""

    def definitions(self, run: Row) -> list[Row]: ...
    async def execute(self, run: Row, name: str, values: Row) -> Row: ...
