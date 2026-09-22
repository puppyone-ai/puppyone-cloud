from collections.abc import AsyncIterator
from typing import Protocol

from src.platform.managed_ai.contracts import MeteredUsage, ProviderEvent
from src.platform.managed_ai.schemas import CompletionRequest


class ProviderStream(Protocol):
    def __aiter__(self) -> AsyncIterator[ProviderEvent]: ...

    async def aclose(self) -> None: ...


class ModelProvider(Protocol):
    def check_available(self) -> None: ...

    async def open(
        self, body: CompletionRequest, *, model_id: str, user_id: str
    ) -> ProviderStream: ...

    async def recover(self, generation_id: str, user_id: str) -> MeteredUsage: ...


class ProviderRejected(Exception):
    """The adapter has positive evidence that execution did not occur."""
