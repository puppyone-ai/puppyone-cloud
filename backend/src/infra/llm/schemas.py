"""
LLM Service Schemas

Pydantic models for LLM requests and responses.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TextModelRequest(BaseModel):
    """Request model for text generation."""

    prompt: str = Field(..., description="User prompt for the model")
    system_prompt: Optional[str] = Field(
        None, description="System prompt to guide model behavior"
    )
    model: Optional[str] = Field(
        None, description="Model to use (defaults to config default)"
    )
    temperature: Optional[float] = Field(
        None, description="Temperature for generation (0.0-2.0)"
    )
    response_format: Optional[Literal["text", "json_object"]] = Field(
        "text", description="Response format: 'text' or 'json_object'"
    )
    max_tokens: Optional[int] = Field(None, description="Maximum tokens to generate")


class TextModelResponse(BaseModel):
    """Response model for text generation."""

    content: str = Field(..., description="Generated content")
    model: str = Field(..., description="Model used for generation")
    usage: dict[str, Any] = Field(..., description="Token usage statistics")
    finish_reason: str = Field(
        ..., description="Reason for completion (stop, length, etc)"
    )


class LLMHealthResponse(BaseModel):
    """Health check response for LLM service."""

    status: Literal["healthy", "degraded", "unhealthy"]
    message: str
    available_models: list[str]
