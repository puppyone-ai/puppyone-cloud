"""OpenRouter's native-token contract, shared by streaming and recovery."""

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StandardUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(ge=0, le=1_000_000, strict=True)
    output_tokens: int = Field(ge=0, le=1_000_000, strict=True)
    cached_tokens: int = Field(ge=0, le=1_000_000, strict=True)
    reasoning_tokens: int | None = Field(default=None, ge=0, le=1_000_000, strict=True)
    cache_write_tokens: int | None = Field(default=None, ge=0, le=1_000_000, strict=True)

    @model_validator(mode="after")
    def subsets(self):
        if self.cached_tokens + (self.cache_write_tokens or 0) > self.input_tokens:
            raise ValueError("Invalid cached input")
        if (self.reasoning_tokens or 0) > self.output_tokens:
            raise ValueError("Invalid reasoning subset")
        return self


def normalize_usage(generation_id: str, data: dict, *, recovered: bool = False) -> dict:
    if recovered:
        values = {
            "input_tokens": data.get("native_tokens_prompt"),
            "output_tokens": data.get("native_tokens_completion"),
            "cached_tokens": data.get("native_tokens_cached"),
            "reasoning_tokens": data.get("native_tokens_reasoning"),
        }
        cost = data.get("total_cost")
    else:
        details = data.get("prompt_tokens_details") or {}
        output_details = data.get("completion_tokens_details") or {}
        values = {
            "input_tokens": data.get("prompt_tokens"),
            "output_tokens": data.get("completion_tokens"),
            "cached_tokens": details.get("cached_tokens"),
            "cache_write_tokens": details.get("cache_write_tokens"),
            "reasoning_tokens": output_details.get("reasoning_tokens"),
        }
        cost = data.get("cost")
    # Missing required counters are not zero. Unknown cache reads could remove
    # a customer discount; retain the hold for recovery instead of guessing.
    usage = StandardUsage.model_validate(values)
    result = {
        "provider_request_id": generation_id,
        "usage_schema_version": "tokens_v1",
        **usage.model_dump(exclude_none=True),
        "cost_source": "generation" if recovered else "stream",
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
