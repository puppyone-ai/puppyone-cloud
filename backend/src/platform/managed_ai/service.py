from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from decimal import Decimal

import anyio
import httpx
from fastapi.responses import StreamingResponse

from src.config import settings
from src.platform.billing.gateway import BillingGatewayError, PuppyPayGateway
from src.platform.managed_ai.schemas import CompletionRequest

PROVIDER_BASE = "https://openrouter.ai/api/v1"
GENERATION_ID = re.compile(r"^[a-zA-Z0-9_.:-]{1,200}$")


def failure(status: int, code: str, message: str) -> BillingGatewayError:
    return BillingGatewayError(status, {"error": {"code": code, "message": message}})


def provider_user(user_id: str) -> str:
    return hashlib.sha256(f"puppyone-ai:{user_id}".encode()).hexdigest()


class ManagedAIService:
    def __init__(
        self,
        gateway: PuppyPayGateway,
        *,
        key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.gateway = gateway
        self.key = (
            key
            if key is not None
            else (settings.MANAGED_AI_OPENROUTER_KEY or os.environ.get("OPENROUTER_API_KEY", ""))
        )
        self.transport = transport

    def client(self) -> httpx.AsyncClient:
        if not self.key:
            raise failure(503, "ai_provider_unconfigured", "Agent model service is unavailable")
        return httpx.AsyncClient(
            base_url=PROVIDER_BASE,
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
            timeout=httpx.Timeout(60, connect=10),
            headers={
                "Authorization": f"Bearer {self.key}",
                "HTTP-Referer": "https://puppyone.ai",
                "X-Title": "PuppyOne Desktop Agent",
            },
        )

    async def completion(self, user_id: str, request_id: str, body: CompletionRequest):
        catalog = await self.gateway.request("GET", "/api/v1/ai/catalog")
        model = next((item for item in catalog["models"] if item["id"] == body.model), None)
        if not model:
            raise failure(400, "ai_model_unavailable", "Select an available Agent model")
        payload = body.model_dump(mode="json", exclude_none=True)
        # Text-only UTF-8 bytes plus per-message/tool framing is a conservative
        # admission estimate, never the final charge. No remote media or tools.
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        input_limit = len(encoded) + 2048 + len(body.messages) * 32 + len(body.tools or []) * 64
        if (
            body.max_tokens > model["max_output_tokens"]
            or input_limit + body.max_tokens > model["context_window"]
        ):
            raise failure(
                400, "ai_context_limit", "Conversation exceeds this model's context or output limit"
            )
        if not self.key:
            raise failure(503, "ai_provider_unconfigured", "Agent model service is unavailable")
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

        client = self.client()
        try:
            # A start claim must never be transparently replayed after a lost
            # response: the second attempt is not permission to generate again.
            await mutate("start", retry=False)
            payload["user"] = provider_user(user_id)
            payload["stream_options"] = {"include_usage": True}
            response = await client.send(
                client.build_request("POST", "/chat/completions", json=payload), stream=True
            )
            if response.status_code != 200:
                # Only explicit pre-execution rejections are safe to release.
                if response.status_code in {400, 401, 402, 403, 404, 413, 422, 429}:
                    await mutate("release", {"reason": "provider_rejected"})
                await response.aclose()
                raise failure(
                    503, "ai_provider_unavailable", "Agent model is temporarily unavailable"
                )
        except BaseException:
            await client.aclose()
            raise

        async def stream():
            generation_id = None
            usage = None
            complete = False
            try:
                async with asyncio.timeout(180):
                    async for line in response.aiter_lines():
                        if len(line) > 1024 * 1024:
                            raise ValueError("Oversized provider frame")
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            complete = True
                            break
                        event = json.loads(data)
                        if event.get("error"):
                            raise ValueError("Provider stream failed")
                        event_id = event.get("id")
                        if event_id:
                            if not GENERATION_ID.fullmatch(event_id) or (
                                generation_id and event_id != generation_id
                            ):
                                raise ValueError("Provider generation identity changed")
                            if not generation_id:
                                # Durable recovery identity before exposing the
                                # first content/tool frame to the local Agent.
                                await mutate("provider", {"provider_request_id": event_id})
                                generation_id = event_id
                        if not generation_id:
                            raise ValueError("Missing provider generation identity")
                        if event.get("usage") is not None:
                            usage = event["usage"]
                        yield f"data: {json.dumps(event)}\n\n".encode()
                    if not complete or not generation_id or not usage or "cost" not in usage:
                        raise ValueError("Provider usage is incomplete")
                    await mutate("settle", usage_payload(generation_id, usage))
                    yield b"data: [DONE]\n\n"
            except Exception:
                # The durable reservation survives cancellation, crashes and
                # billing outages. Worker recovery settles provider metadata.
                yield b'data: {"error":{"code":"ai_usage_pending","message":"The model request was interrupted. Balance will refresh after usage is reconciled."}}\n\n'
            finally:
                with anyio.CancelScope(shield=True):
                    await response.aclose()
                    await client.aclose()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def recover_usage(self, generation_id: str, user_id: str) -> dict:
        if not GENERATION_ID.fullmatch(generation_id):
            raise failure(400, "ai_generation_invalid", "Invalid generation identity")
        async with self.client() as client:
            response = await client.get("/generation", params={"id": generation_id})
        if response.status_code != 200:
            raise failure(503, "ai_usage_pending", "Provider usage is not available yet")
        data = response.json()["data"]
        if data.get("id") != generation_id or data.get("external_user") != provider_user(user_id):
            raise failure(409, "ai_generation_mismatch", "Provider usage identity does not match")
        if data.get("total_cost") is None or not (
            data.get("finish_reason") or data.get("cancelled")
        ):
            raise failure(503, "ai_usage_pending", "Provider usage is not final")
        return {
            "provider_request_id": generation_id,
            "provider_cost_usd": str(Decimal(str(data["total_cost"]))),
            "input_tokens": data["native_tokens_prompt"],
            "output_tokens": data["native_tokens_completion"],
            "cached_tokens": data.get("native_tokens_cached") or 0,
        }


def usage_payload(generation_id: str, usage: dict) -> dict:
    return {
        "provider_request_id": generation_id,
        "provider_cost_usd": str(Decimal(str(usage["cost"]))),
        "input_tokens": usage["prompt_tokens"],
        "output_tokens": usage["completion_tokens"],
        "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
    }
