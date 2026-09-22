from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrialClaimRequest(StrictModel):
    pass


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


class ReasoningDetail(StrictModel):
    id: str | None = None
    format: str | None = None
    index: int | None = Field(default=None, ge=0, strict=True)


class ReasoningText(ReasoningDetail):
    type: Literal["reasoning.text"]
    text: str
    signature: str | None = None


class ReasoningSummary(ReasoningDetail):
    type: Literal["reasoning.summary"]
    summary: str


class ReasoningEncrypted(ReasoningDetail):
    type: Literal["reasoning.encrypted"]
    data: str


class Message(StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[TextPart] | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = Field(default=None, max_length=256)
    name: str | None = Field(default=None, max_length=128)
    # Pi replays the provider's reasoning alongside assistant tool calls and
    # subsequent turns. Keep it in model context, never as a transcript event.
    reasoning_content: str | None = None
    reasoning: str | None = None
    reasoning_text: str | None = None
    reasoning_details: (
        list[
            Annotated[
                ReasoningText | ReasoningSummary | ReasoningEncrypted, Field(discriminator="type")
            ]
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def assistant_reasoning(self):
        context = (
            self.reasoning_content,
            self.reasoning,
            self.reasoning_text,
            self.reasoning_details,
        )
        if any(value is not None for value in context) and self.role != "assistant":
            raise ValueError("Reasoning context requires an assistant message")
        return self


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
