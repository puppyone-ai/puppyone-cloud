from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextPart(StrictModel):
    type: Literal["text"]
    text: str


class FunctionCall(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    arguments: str


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=256)
    type: Literal["function"]
    function: FunctionCall


class Message(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[TextPart] | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = Field(default=None, max_length=256)
    name: str | None = Field(default=None, max_length=128)


class FunctionDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    parameters: dict
    strict: bool | None = None


class Tool(StrictModel):
    type: Literal["function"]
    function: FunctionDefinition


class StreamOptions(StrictModel):
    include_usage: bool = True


class CompletionRequest(StrictModel):
    model: str = Field(min_length=1, max_length=200)
    messages: list[Message] = Field(min_length=1, max_length=256)
    tools: list[Tool] | None = Field(default=None, max_length=128)
    tool_choice: Literal["auto", "none", "required"] | None = None
    parallel_tool_calls: bool | None = None
    stream: Literal[True] = True
    stream_options: StreamOptions | None = None
    max_tokens: int = Field(
        default=4096,
        ge=1,
        le=32768,
        validation_alias=AliasChoices("max_tokens", "max_completion_tokens"),
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)


class CheckoutRequest(StrictModel):
    pack_id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
