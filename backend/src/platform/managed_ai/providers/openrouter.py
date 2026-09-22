"""OpenRouter HTTP, stream dialect, identity and native-token recovery."""

import hashlib
import json
import re

import anyio
import httpx

from src.platform.managed_ai.contracts import InferenceError, MeteredUsage, ProviderEvent
from src.platform.managed_ai.providers.base import ProviderRejected
from src.platform.managed_ai.providers.openrouter_usage import normalize_usage, public_frame
from src.platform.managed_ai.schemas import CompletionRequest

GENERATION_ID = re.compile(r"^[a-zA-Z0-9_.:-]{1,200}$")


def provider_user(user_id: str) -> str:
    return hashlib.sha256(f"puppyone-ai:{user_id}".encode()).hexdigest()


class OpenRouterProvider:
    def __init__(self, *, key: str, transport: httpx.AsyncBaseTransport | None = None):
        self._key = key
        self._transport = transport

    def check_available(self):
        if not self._key:
            raise InferenceError(
                503, "ai_provider_unconfigured", "Agent model service is unavailable"
            )

    def _client(self):
        self.check_available()
        return httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            trust_env=False,
            follow_redirects=False,
            transport=self._transport,
            timeout=httpx.Timeout(60, connect=10),
            headers={
                "Authorization": f"Bearer {self._key}",
                "HTTP-Referer": "https://puppyone.ai",
                "X-Title": "PuppyOne Desktop Agent",
            },
        )

    async def open(self, body: CompletionRequest, *, model_id: str, user_id: str):
        payload = body.model_dump(mode="json", exclude_none=True)
        payload.update(
            model=model_id, user=provider_user(user_id), stream_options={"include_usage": True}
        )
        client = self._client()
        try:
            response = await client.send(
                client.build_request("POST", "/chat/completions", json=payload), stream=True
            )
            if response.status_code != 200:
                await response.aclose()
                if response.status_code in {400, 401, 402, 403, 404, 413, 422, 429}:
                    raise ProviderRejected()
                raise InferenceError(
                    503, "ai_provider_unavailable", "Agent model is temporarily unavailable"
                )
            return OpenRouterStream(client, response)
        except BaseException as error:
            with anyio.CancelScope(shield=True):
                await client.aclose()
            if isinstance(error, httpx.HTTPError):
                raise InferenceError(
                    503, "ai_provider_unavailable", "Agent model is temporarily unavailable"
                ) from None
            raise

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


class OpenRouterStream:
    def __init__(self, client, response):
        self._client = client
        self._response = response
        self._closed = False

    async def aclose(self):
        if not self._closed:
            self._closed = True
            with anyio.CancelScope(shield=True):
                try:
                    await self._response.aclose()
                finally:
                    await self._client.aclose()

    async def __aiter__(self):
        generation_id = None
        usage = None
        async for line in self._response.aiter_lines():
            if len(line) > 1024 * 1024:
                raise ValueError("Oversized provider frame")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                if not generation_id or usage is None:
                    raise ValueError("Provider usage is incomplete")
                yield ProviderEvent(
                    generation_id,
                    usage=MeteredUsage.model_validate(normalize_usage(generation_id, usage)),
                )
                return
            event = json.loads(data)
            if event.get("error"):
                raise ValueError("Provider stream failed")
            event_id = event.get("id")
            if event_id:
                if not GENERATION_ID.fullmatch(event_id) or (
                    generation_id and event_id != generation_id
                ):
                    raise ValueError("Provider generation identity changed")
                generation_id = event_id
            if not generation_id:
                raise ValueError("Missing provider generation identity")
            if event.get("usage") is not None:
                usage = event["usage"]
            yield ProviderEvent(generation_id, frame=public_frame(event))
        raise ValueError("Provider stream ended without completion")
