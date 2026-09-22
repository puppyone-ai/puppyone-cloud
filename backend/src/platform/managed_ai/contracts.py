"""Provider-neutral inference and metering contracts; no HTTP or vendor imports."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,32}$")
    provider_account_ref: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    provider_model_id: str = Field(min_length=1, max_length=200)

    def identity(self) -> dict:
        return self.model_dump(exclude={"provider_model_id"})


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


class MeteredUsage(StandardUsage):
    provider_request_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,200}$")
    usage_schema_version: Literal["tokens_v1"] = "tokens_v1"
    cost_source: Literal["stream", "generation"]
    provider_cost_usd: str | None = None


@dataclass(frozen=True)
class ProviderEvent:
    generation_id: str
    # Client-protocol chunk, already translated/sanitized by the adapter.
    frame: dict | None = None
    # Present only after confirmed provider completion, never a partial estimate.
    usage: MeteredUsage | None = None


@dataclass(frozen=True)
class ModelChunk:
    frame: dict


@dataclass(frozen=True)
class InferenceDone:
    pass


@dataclass(frozen=True)
class InferenceFailed:
    code: str
    message: str


@dataclass
class InferenceRun:
    reservation_id: str
    events: AsyncGenerator[ModelChunk | InferenceDone | InferenceFailed, None]
    close_provider: Callable[[], Awaitable[None]]

    async def aclose(self):
        try:
            await self.events.aclose()
        finally:
            await self.close_provider()


@dataclass(frozen=True)
class InferenceError(Exception):
    status_code: int
    code: str
    message: str

    @property
    def payload(self):
        return {"error": {"code": self.code, "message": self.message}}
