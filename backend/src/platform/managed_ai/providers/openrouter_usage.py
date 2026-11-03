"""OpenRouter's native-token contract, shared by streaming and recovery."""

from src.platform.managed_ai.providers.usage import metered_usage, stream_usage


def normalize_usage(generation_id: str, data: dict, *, recovered: bool = False) -> dict:
    if not recovered:
        return stream_usage(generation_id, data)
    values = {
        "input_tokens": data.get("native_tokens_prompt"),
        "output_tokens": data.get("native_tokens_completion"),
        "cached_tokens": data.get("native_tokens_cached"),
        "reasoning_tokens": data.get("native_tokens_reasoning"),
    }
    return metered_usage(
        generation_id, values, cost=data.get("total_cost"), cost_source="generation"
    )
