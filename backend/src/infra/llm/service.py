"""
LLM Service

Core service for interacting with text models via litellm.

Note: Importing the litellm library is very slow (20s+), so we use a lazy-loading strategy,
only importing it when an LLM call is actually made, to improve app startup and reload speed.
"""

import asyncio
import json
import logging
from typing import Any, Literal, Optional

from src.infra.llm.config import llm_config
from src.infra.llm.exceptions import (
    APIKeyError,
    InvalidResponseError,
    LLMError,
    ModelNotFoundError,
    RateLimitError,
    TimeoutError,
)
from src.infra.llm.schemas import TextModelRequest, TextModelResponse

logger = logging.getLogger(__name__)


class LLMService:
    """Service for interacting with LLM models."""

    def __init__(self):
        """Initialize LLM service with configuration."""
        self.config = llm_config
        self.default_model = self.config.default_text_model
        self.supported_models = self.config.supported_text_models
        self._litellm_loaded = False
        self._acompletion = None
        logger.info(
            f"LLMService initialized with default model: {self.default_model} (litellm not loaded yet)"
        )

    def _ensure_litellm(self):
        """
        Ensure litellm is loaded (lazy initialization).

        Only imports litellm on the first LLM call, avoiding loading this heavy library at app startup.
        This reduces startup time from ~43s to ~20s.
        """
        if not self._litellm_loaded:
            logger.info(
                "Lazy-loading litellm library (this may take a few seconds on first use)..."
            )
            start_time = (
                asyncio.get_event_loop().time()
                if asyncio.get_event_loop().is_running()
                else 0
            )

            from litellm import acompletion

            self._acompletion = acompletion
            self._litellm_loaded = True

            duration = (
                (asyncio.get_event_loop().time() - start_time) * 1000
                if start_time
                else 0
            )
            logger.info(f"litellm loaded successfully (took {duration:.2f}ms)")

    async def call_text_model(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        response_format: Literal["text", "json_object"] = "text",
        max_tokens: Optional[int] = None,
    ) -> TextModelResponse:
        """
        Call a text model with the given prompt.

        Args:
            prompt: User prompt for the model
            system_prompt: Optional system prompt to guide model behavior
            model: Model to use (defaults to config default)
            temperature: Temperature for generation (defaults to config default)
            response_format: Response format - 'text' or 'json_object'
            max_tokens: Maximum tokens to generate

        Returns:
            TextModelResponse with generated content and metadata

        Raises:
            ModelNotFoundError: If requested model is not supported
            APIKeyError: If API key is missing or invalid
            TimeoutError: If request times out
            RateLimitError: If rate limit is exceeded
            LLMError: For other errors
        """
        # Lazy-load litellm (only loaded on first use)
        self._ensure_litellm()

        # Import exception types (also need lazy loading)
        from litellm.exceptions import (
            APIError,
            AuthenticationError,
            RateLimitError as LiteLLMRateLimitError,
            Timeout,
        )

        # Validate model
        model = model or self.default_model
        if model not in self.supported_models:
            raise ModelNotFoundError(model, self.supported_models)

        # Never let litellm fall back to a provider chosen from ambient credentials.
        api_key = self.config.llm_api_key.get_secret_value()
        if not api_key or not self.config.llm_api_base:
            raise APIKeyError(model.split("/")[0])

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Prepare request parameters
        temperature = (
            temperature if temperature is not None else self.config.llm_temperature
        )

        request_params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "timeout": self.config.llm_timeout,
            "api_base": self.config.llm_api_base,
            "api_key": api_key,
        }

        # Add response format for JSON mode
        if response_format == "json_object":
            request_params["response_format"] = {"type": "json_object"}

        if max_tokens:
            request_params["max_tokens"] = max_tokens

        # Retry logic
        last_error = None
        for attempt in range(self.config.llm_max_retries):
            try:
                logger.info(
                    f"Calling LLM (attempt {attempt + 1}/{self.config.llm_max_retries}): "
                    f"model={model}, response_format={response_format}"
                )

                # Use the lazy-loaded acompletion
                response = await self._acompletion(**request_params)

                # Extract response content
                message = response.choices[0].message
                content = message.content

                # Handle None or empty content (some models use reasoning_content)
                if not content:
                    # For reasoning models (like Qwen3), check reasoning_content
                    if (
                        hasattr(message, "reasoning_content")
                        and message.reasoning_content
                    ):
                        logger.info(
                            "Model returned empty content but has reasoning_content. "
                            "This may indicate the model is in reasoning mode and needs more tokens."
                        )
                        content = message.reasoning_content
                    else:
                        logger.warning(
                            "Model returned empty content with no reasoning_content"
                        )
                        content = ""

                # Validate JSON format if requested
                if response_format == "json_object":
                    try:
                        json.loads(content)  # Validate it's valid JSON
                    except json.JSONDecodeError as e:
                        raise InvalidResponseError("json_object", content) from e

                # Build response
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

                logger.info(
                    f"LLM call successful: model={model}, "
                    f"tokens={usage['total_tokens']}, "
                    f"finish_reason={response.choices[0].finish_reason}"
                )

                return TextModelResponse(
                    content=content,
                    model=model,
                    usage=usage,
                    finish_reason=response.choices[0].finish_reason,
                )

            except AuthenticationError as e:
                # Extract provider from model
                provider = model.split("/")[0] if "/" in model else "unknown"
                logger.error(f"Authentication error for provider {provider}: {e}")
                raise APIKeyError(provider) from e

            except Timeout as e:
                logger.warning(f"Timeout on attempt {attempt + 1}: {e}")
                last_error = TimeoutError(self.config.llm_timeout)
                if attempt < self.config.llm_max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff
                continue

            except LiteLLMRateLimitError as e:
                logger.warning(f"Rate limit exceeded on attempt {attempt + 1}: {e}")
                # Try to extract retry_after from error
                retry_after = getattr(e, "retry_after", None)
                last_error = RateLimitError(retry_after)
                if attempt < self.config.llm_max_retries - 1:
                    wait_time = retry_after if retry_after else (2**attempt)
                    await asyncio.sleep(wait_time)
                continue

            except APIError as e:
                logger.error(f"API error on attempt {attempt + 1}: {e}")
                last_error = LLMError(f"API error: {e!s}", original_error=e)
                if attempt < self.config.llm_max_retries - 1:
                    await asyncio.sleep(2**attempt)
                continue

            except Exception as e:
                logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
                last_error = LLMError(f"Unexpected error: {e!s}", original_error=e)
                if attempt < self.config.llm_max_retries - 1:
                    await asyncio.sleep(2**attempt)
                continue

        # All retries exhausted
        logger.error(f"All {self.config.llm_max_retries} retries exhausted")
        if last_error:
            raise last_error
        raise LLMError("All retries exhausted with unknown error")

    async def call_text_model_from_request(
        self, request: TextModelRequest
    ) -> TextModelResponse:
        """
        Call text model using a TextModelRequest object.

        Args:
            request: TextModelRequest with all parameters

        Returns:
            TextModelResponse with generated content
        """
        return await self.call_text_model(
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            model=request.model,
            temperature=request.temperature,
            response_format=request.response_format or "text",
            max_tokens=request.max_tokens,
        )

    def get_supported_models(self) -> list[str]:
        """Get list of supported models."""
        return self.supported_models.copy()

    def is_model_supported(self, model: str) -> bool:
        """Check if a model is supported."""
        return model in self.supported_models
