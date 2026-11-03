"""Usage metering and client frames shared by OpenAI-compatible streams."""

from decimal import Decimal, InvalidOperation
from typing import Literal

from src.platform.managed_ai.contracts import StandardUsage
from src.shared_types.managed_ai_provider import DEFAULT_CACHED_TOKENS_PATH


def read_path(data: dict, path: str):
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def metered_usage(
    generation_id: str, values: dict, *, cost, cost_source: Literal["stream", "generation"]
) -> dict:
    # Missing required counters are not zero. Unknown cache reads could remove
    # a customer discount; retain the hold for recovery instead of guessing.
    usage = StandardUsage.model_validate(values)
    result = {
        "provider_request_id": generation_id,
        "usage_schema_version": "tokens_v1",
        **usage.model_dump(exclude_none=True),
        "cost_source": cost_source,
    }
    # Internal cost evidence must never prevent metered customer settlement.
    if cost is not None:
        try:
            amount = Decimal(str(cost))
            if amount.is_finite() and 0 <= amount <= 1000:
                result["provider_cost_usd"] = str(amount)
        except (InvalidOperation, ValueError):
            pass
    return result


def stream_usage(
    generation_id: str,
    data: dict,
    *,
    cached_tokens: str | Literal[0] = DEFAULT_CACHED_TOKENS_PATH,
) -> dict:
    values = {
        "input_tokens": data.get("prompt_tokens"),
        "output_tokens": data.get("completion_tokens"),
        "cached_tokens": 0 if cached_tokens == 0 else read_path(data, cached_tokens),
        "cache_write_tokens": read_path(data, "prompt_tokens_details.cache_write_tokens"),
        "reasoning_tokens": read_path(data, "completion_tokens_details.reasoning_tokens"),
    }
    return metered_usage(generation_id, values, cost=data.get("cost"), cost_source="stream")


def public_frame(event: dict) -> dict:
    result = dict(event)
    result.pop("cost", None)
    result.pop("cost_details", None)
    if isinstance(result.get("usage"), dict):
        # Only token fields cross the customer boundary, never supplier prices.
        result["usage"] = {
            key: value
            for key, value in result["usage"].items()
            if key
            in {
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_tokens_details",
                "completion_tokens_details",
            }
        }
    return result
