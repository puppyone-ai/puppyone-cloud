"""OpenRouter's route profile and native-token usage recovery."""

import httpx

from src.platform.managed_ai.contracts import InferenceError, MeteredUsage
from src.platform.managed_ai.providers.openai_compatible import (
    GENERATION_ID,
    OpenAICompatibleProvider,
    provider_user,
)
from src.platform.managed_ai.providers.openrouter_usage import normalize_usage
from src.shared_types.managed_ai_provider import ManagedAIProviderProfile

OPENROUTER = ManagedAIProviderProfile(
    provider="openrouter",
    base_url="https://openrouter.ai/api/v1",
    api_key_env="MANAGED_AI_OPENROUTER_KEY",
    headers={"HTTP-Referer": "https://puppyone.ai", "X-Title": "PuppyOne Desktop Agent"},
)


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(self, *, key: str, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(OPENROUTER, key=key, transport=transport)

    async def recover(self, generation_id: str, user_id: str) -> MeteredUsage:
        if not GENERATION_ID.fullmatch(generation_id):
            raise InferenceError(400, "ai_generation_invalid", "Invalid generation identity")
        try:
            async with self._client() as client:
                response = await client.get("/generation", params={"id": generation_id})
        except httpx.HTTPError:
            raise InferenceError(
                503, "ai_usage_pending", "Provider usage is not available yet"
            ) from None
        if response.status_code != 200:
            raise InferenceError(503, "ai_usage_pending", "Provider usage is not available yet")
        data = response.json()["data"]
        if data.get("id") != generation_id or data.get("external_user") != provider_user(user_id):
            raise InferenceError(
                409, "ai_generation_mismatch", "Provider usage identity does not match"
            )
        if not (data.get("finish_reason") or data.get("cancelled")):
            raise InferenceError(503, "ai_usage_pending", "Provider usage is not final")
        try:
            return MeteredUsage.model_validate(normalize_usage(generation_id, data, recovered=True))
        except ValueError:
            raise InferenceError(
                503, "ai_usage_pending", "Provider token usage is incomplete"
            ) from None
