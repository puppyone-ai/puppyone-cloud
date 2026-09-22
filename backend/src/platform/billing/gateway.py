from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import settings


@dataclass(frozen=True)
class BillingGatewayError(Exception):
    status_code: int
    payload: dict[str, Any]


_ALLOWED_PATHS = (
    re.compile(
        r"^/api/v1/ai/(?:catalog|balance|trial|checkouts|(?:purchases|usage)/[a-f0-9-]{36})$"
    ),
    re.compile(r"^/internal/v1/ai/reservations$"),
    re.compile(r"^/internal/v1/ai/reservations/[a-f0-9-]{36}/(?:start|provider|settle|release)$"),
    re.compile(r"^/api/v1/billing/catalog$"),
    re.compile(
        r"^/api/v1/billing/organizations/[^/?#]+/"
        r"(?:summary|usage|plan/quote|seats/quote|checkout|plan/change|"
        r"seats/change|subscription/cancel|portal|runtime/top-up|runtime/overage)$"
    ),
    re.compile(r"^/api/v1/billing/organizations/[^/?#]+/quotes/[^/?#]+$"),
    re.compile(r"^/internal/v1/billing/seat-proposals$"),
    re.compile(r"^/internal/v1/billing/organizations/provision$"),
    re.compile(r"^/internal/v1/billing/runtime/reservations$"),
    re.compile(
        r"^/internal/v1/billing/runtime/reservations/[^/?#]+/"
        r"(?:heartbeat|settle|cancel)$"
    ),
)
_MAX_RESPONSE_BYTES = 1024 * 1024


def _path_is_allowed(path: str) -> bool:
    if any(segment in {".", ".."} for segment in path.split("/")):
        return False
    return any(pattern.fullmatch(path) for pattern in _ALLOWED_PATHS)


class PuppyPayGateway:
    """Small, allow-listed HTTP boundary around the private PuppyPay service."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        internal_secret: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url if base_url is not None else settings.PUPPYPAY_BASE_URL).rstrip(
            "/"
        )
        self._internal_secret = (
            internal_secret
            if internal_secret is not None
            else settings.PUPPYPAY_INTERNAL_API_SECRET
        )
        self._timeout = timeout_seconds or settings.PUPPYPAY_TIMEOUT_SECONDS
        self._transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        actor_user_id: str | None = None,
        actor_email: str | None = None,
        idempotency_key: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._base_url:
            raise BillingGatewayError(
                503,
                {"error": {"code": "billing_unconfigured", "message": "Billing is unavailable"}},
            )
        if not _path_is_allowed(path):
            raise ValueError("PuppyPay path is outside the billing API allow-list")
        headers = {"Accept": "application/json", "User-Agent": "PuppyOne-Billing-BFF/1.0"}
        if self._internal_secret:
            headers["X-Internal-Secret"] = self._internal_secret
        if actor_user_id:
            headers["X-PuppyOne-User-ID"] = actor_user_id
        if actor_email:
            headers["X-PuppyOne-User-Email"] = actor_email
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        retry_safe = method.upper() == "GET" or bool(idempotency_key)
        maximum_attempts = 2 if retry_safe else 1
        response: httpx.Response | None = None
        last_error: httpx.HTTPError | None = None
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        ) as client:
            for attempt in range(maximum_attempts):
                try:
                    response = await client.request(method, path, headers=headers, json=body)
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt + 1 < maximum_attempts:
                        await asyncio.sleep(0.05)
                        continue
                    break
                if response.status_code in {502, 503, 504} and attempt + 1 < maximum_attempts:
                    await asyncio.sleep(0.05)
                    continue
                break
        if response is None:
            raise BillingGatewayError(
                503,
                {
                    "error": {
                        "code": "billing_upstream_unavailable",
                        "message": "Billing service is temporarily unavailable",
                        "retryable": True,
                    }
                },
            ) from last_error
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise BillingGatewayError(
                502,
                {
                    "error": {
                        "code": "billing_upstream_response_too_large",
                        "message": "Billing service returned an oversized response",
                    }
                },
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise BillingGatewayError(
                502,
                {
                    "error": {
                        "code": "billing_upstream_invalid_response",
                        "message": "Billing service returned an invalid response",
                    }
                },
            ) from exc
        if not isinstance(payload, dict):
            raise BillingGatewayError(
                502,
                {
                    "error": {
                        "code": "billing_upstream_invalid_response",
                        "message": "Billing service returned an invalid response",
                    }
                },
            )
        if response.status_code >= 400:
            status_code = 503 if response.status_code >= 500 else response.status_code
            raise BillingGatewayError(status_code, payload)
        return payload


def get_billing_gateway() -> PuppyPayGateway:
    return PuppyPayGateway()
