"""
LLM Service Exceptions

Custom exceptions for LLM service errors.
"""


class LLMError(Exception):
    """Base exception for LLM service errors."""

    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.original_error = original_error


class ModelNotFoundError(LLMError):
    """Exception raised when requested model is not supported."""

    def __init__(self, model: str, available_models: list[str]):
        message = f"Model '{model}' is not supported. Available models: {', '.join(available_models)}"
        super().__init__(message)
        self.model = model
        self.available_models = available_models


class APIKeyError(LLMError):
    """Exception raised when API key is missing or invalid."""

    def __init__(self, provider: str):
        message = f"API key for provider '{provider}' is missing or invalid. Please set the appropriate environment variable."
        super().__init__(message)
        self.provider = provider


class TimeoutError(LLMError):
    """Exception raised when LLM API call times out."""

    def __init__(self, timeout: int):
        message = f"LLM API call timed out after {timeout} seconds."
        super().__init__(message)
        self.timeout = timeout


class RateLimitError(LLMError):
    """Exception raised when rate limit is exceeded."""

    def __init__(self, retry_after: int | None = None):
        message = "Rate limit exceeded."
        if retry_after:
            message += f" Retry after {retry_after} seconds."
        super().__init__(message)
        self.retry_after = retry_after


class InvalidResponseError(LLMError):
    """Exception raised when LLM returns invalid response format."""

    def __init__(self, expected_format: str, actual_response: str):
        message = (
            f"Expected response format '{expected_format}', but got invalid response."
        )
        super().__init__(message)
        self.expected_format = expected_format
        self.actual_response = actual_response
