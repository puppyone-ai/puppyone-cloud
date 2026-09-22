"""Provider-independent admission, execution identity and metered settlement."""

import asyncio
import hashlib
import json

import anyio
from pydantic import ValidationError

from src.platform.billing.gateway import PuppyPayGateway
from src.platform.managed_ai.contracts import (
    InferenceDone,
    InferenceError,
    InferenceFailed,
    InferenceRun,
    ModelChunk,
    ProviderRoute,
)
from src.platform.managed_ai.providers.base import ProviderRejected
from src.platform.managed_ai.providers.registry import ProviderRegistry
from src.platform.managed_ai.schemas import CompletionRequest


class ManagedAIService:
    def __init__(self, gateway: PuppyPayGateway, *, providers: ProviderRegistry):
        self.gateway = gateway
        self.providers = providers

    async def completion(
        self, user_id: str, request_id: str, body: CompletionRequest
    ) -> InferenceRun:
        catalog = await self.gateway.request("GET", "/api/v1/ai/catalog")
        model = next((item for item in catalog["models"] if item["id"] == body.model), None)
        if not model:
            raise InferenceError(400, "ai_model_unavailable", "Select an available Agent model")
        payload = body.model_dump(mode="json", exclude_none=True)
        # Conservative admission only; actual tokens determine settlement.
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        input_limit = len(encoded) + 2048 + len(body.messages) * 32 + len(body.tools or []) * 64
        if (
            body.max_tokens > model["max_output_tokens"]
            or input_limit + body.max_tokens > model["context_window"]
        ):
            raise InferenceError(
                400, "ai_context_limit", "Conversation exceeds this model's context or output limit"
            )
        reservation = await self.gateway.request(
            "POST",
            "/internal/v1/ai/reservations",
            idempotency_key=request_id,
            body={
                "user_id": user_id,
                "request_id": request_id,
                "request_hash": hashlib.sha256(encoded).hexdigest(),
                "model_id": body.model,
                "input_token_limit": input_limit,
                "output_token_limit": body.max_tokens,
            },
        )
        path = f"/internal/v1/ai/reservations/{reservation['reservation_id']}"

        async def mutate(action, values=None, *, retry=True):
            return await self.gateway.request(
                "POST",
                f"{path}/{action}",
                idempotency_key=f"{request_id}:{action}" if retry else None,
                body={"user_id": user_id, **(values or {})},
            )

        try:
            # Locked by Pay; never supplied by Desktop or reselected mid-request.
            route = ProviderRoute.model_validate(reservation.get("provider_route"))
            provider = self.providers.resolve(route.provider, route.provider_account_ref)
        except (ValidationError, InferenceError):
            await mutate("release", {"reason": "not_started"})
            raise InferenceError(
                503, "ai_route_unavailable", "Agent model route is unavailable"
            ) from None
        identity = route.identity()
        # Never replay a lost start response, nor fall back after uncertain execution.
        await mutate("start", retry=False)
        try:
            upstream = await provider.open(body, model_id=route.provider_model_id, user_id=user_id)
        except ProviderRejected:
            await mutate("release", {"reason": "provider_rejected"})
            raise InferenceError(
                503, "ai_provider_unavailable", "Agent model is temporarily unavailable"
            ) from None

        async def stream():
            generation_id = None
            usage = None
            try:
                async with asyncio.timeout(180):
                    async for event in upstream:
                        if not event.generation_id or (
                            generation_id and generation_id != event.generation_id
                        ):
                            raise ValueError("Provider generation identity changed")
                        if generation_id is None:
                            # Persist identity before exposing any text or tool call.
                            await mutate(
                                "provider", {"provider_request_id": event.generation_id, **identity}
                            )
                            generation_id = event.generation_id
                        if event.frame is not None:
                            yield ModelChunk(event.frame)
                        if event.usage is not None:
                            if event.usage.provider_request_id != generation_id:
                                raise ValueError("Usage identity does not match")
                            usage = event.usage
                            break
                    if usage is None:
                        raise ValueError("Provider usage is incomplete")
                    await mutate("settle", {**usage.model_dump(exclude_none=True), **identity})
                    yield InferenceDone()
            except Exception:
                # Unknown outcome stays held; recover against the persisted route.
                yield InferenceFailed(
                    "ai_usage_pending",
                    "The model request was interrupted. Balance will refresh after usage is reconciled.",
                )
            finally:
                with anyio.CancelScope(shield=True):
                    await upstream.aclose()

        return InferenceRun(reservation["reservation_id"], stream(), upstream.aclose)

    async def recover_usage(
        self, generation_id: str, user_id: str, *, provider: str, provider_account_ref: str
    ) -> dict:
        adapter = self.providers.resolve(provider, provider_account_ref)
        usage = await adapter.recover(generation_id, user_id)
        if usage.provider_request_id != generation_id:
            raise InferenceError(
                409, "ai_generation_mismatch", "Provider usage identity does not match"
            )
        return {
            **usage.model_dump(exclude_none=True),
            "provider": provider,
            "provider_account_ref": provider_account_ref,
        }
