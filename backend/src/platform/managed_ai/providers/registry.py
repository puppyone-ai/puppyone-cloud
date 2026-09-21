from types import MappingProxyType

from src.platform.managed_ai.contracts import InferenceError
from src.platform.managed_ai.providers.base import ModelProvider


class ProviderRegistry:
    def __init__(self, providers: dict[tuple[str, str], ModelProvider]):
        self._providers = MappingProxyType(dict(providers))

    def resolve(self, provider: str, account_ref: str) -> ModelProvider:
        adapter = self._providers.get((provider, account_ref))
        if adapter is None:
            raise InferenceError(503, "ai_route_unavailable", "Agent model route is unavailable")
        adapter.check_available()
        return adapter
