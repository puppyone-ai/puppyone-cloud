"""OpenAI-compatible HTTP, stream dialect and caller identity."""

import hashlib
import json
import logging
import re

import anyio
import httpx

from src.platform.managed_ai.contracts import InferenceError, MeteredUsage, ProviderEvent
from src.platform.managed_ai.providers.base import ProviderRejected
from src.platform.managed_ai.providers.usage import public_frame, stream_usage
from src.platform.managed_ai.schemas import CompletionRequest
from src.shared_types.managed_ai_provider import ManagedAIProviderProfile

GENERATION_ID = re.compile(r"^[a-zA-Z0-9_.:-]{1,200}$")

logger = logging.getLogger(__name__)


def provider_user(user_id: str) -> str:
    return hashlib.sha256(f"puppyone-ai:{user_id}".encode()).hexdigest()


class OpenAICompatibleProvider:
    def __init__(
        self,
        profile: ManagedAIProviderProfile,
        *,
        key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._profile = profile
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
            base_url=self._profile.base_url,
            trust_env=False,
            follow_redirects=False,
            transport=self._transport,
            timeout=httpx.Timeout(60, connect=10),
            headers={"Authorization": f"Bearer {self._key}", **self._profile.headers},
        )

    def _payload(self, body: CompletionRequest, *, model_id: str, user_id: str) -> dict:
        payload = body.model_dump(mode="json", exclude_none=True)
        payload.update(
            model=model_id, user=provider_user(user_id), stream_options={"include_usage": True}
        )
        dropped = set(self._profile.drop_fields)
        if dropped:
            payload = {key: value for key, value in payload.items() if key not in dropped}
            payload["messages"] = [
                {key: value for key, value in message.items() if key not in dropped}
                for message in payload["messages"]
            ]
        for source, target in self._profile.rename_fields.items():
            if source in payload:
                payload[target] = payload.pop(source)
        return payload

    def _log_failure(self, **evidence):
        # Status only: a provider error body can echo the customer's prompt.
        logger.warning(
            "managed_ai_provider_request_failed",
            extra={
                "provider": self._profile.provider,
                "provider_account_ref": self._profile.account_ref,
                **evidence,
            },
        )

    async def open(self, body: CompletionRequest, *, model_id: str, user_id: str):
        payload = self._payload(body, model_id=model_id, user_id=user_id)
        client = self._client()
        try:
            response = await client.send(
                client.build_request("POST", "/chat/completions", json=payload), stream=True
            )
            if response.status_code != 200:
                await response.aclose()
                self._log_failure(status_code=response.status_code)
                if response.status_code in {400, 401, 402, 403, 404, 413, 422, 429}:
                    raise ProviderRejected()
                raise InferenceError(
                    503, "ai_provider_unavailable", "Agent model is temporarily unavailable"
                )
            return OpenAICompatibleStream(
                client, response, cached_tokens=self._profile.cached_tokens
            )
        except BaseException as error:
            with anyio.CancelScope(shield=True):
                await client.aclose()
            if isinstance(error, httpx.HTTPError):
                self._log_failure(error_type=type(error).__name__)
                raise InferenceError(
                    503, "ai_provider_unavailable", "Agent model is temporarily unavailable"
                ) from None
            raise

    async def recover(self, generation_id: str, user_id: str) -> MeteredUsage:
        # No provider lookup exists, so the hold stays until PuppyPay's retries
        # move it to manual review. Interrupted usage is never estimated.
        raise InferenceError(503, "ai_usage_pending", "Provider usage is not available yet")


class OpenAICompatibleStream:
    def __init__(self, client, response, *, cached_tokens):
        self._client = client
        self._response = response
        self._cached_tokens = cached_tokens
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
                    usage=MeteredUsage.model_validate(
                        stream_usage(generation_id, usage, cached_tokens=self._cached_tokens)
                    ),
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
