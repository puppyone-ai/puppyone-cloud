from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanQuoteRequest(StrictRequest):
    target_plan_id: str = Field(min_length=1)
    seat_quantity: int = Field(gt=0)


class SeatQuoteRequest(StrictRequest):
    seat_quantity: int = Field(gt=0)
    operation_id: str | None = Field(default=None, min_length=1)


class CheckoutRequest(StrictRequest):
    plan_id: str = Field(min_length=1)
    seat_quantity: int = Field(default=1, gt=0)
    quote_id: str | None = Field(default=None, min_length=1, max_length=255)
    operation_id: str | None = Field(default=None, min_length=1)


class QuoteApplyRequest(StrictRequest):
    quote_id: str = Field(min_length=1, max_length=255)
    operation_id: str | None = Field(default=None, min_length=1)


class CancellationRequest(StrictRequest):
    cancel_at_period_end: bool = True


class TopUpRequest(StrictRequest):
    pack_id: str = Field(min_length=1)


class RuntimeOverageRequest(StrictRequest):
    enabled: bool
    monthly_limit_cents: int = Field(ge=0, le=10_000_000)
