"""
LLM Service Configuration

Manages configuration for LLM models including API keys, model settings, and timeouts.
"""

import os

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"
_ENV_FILE_FOR_SETTINGS = (
    _ENV_FILE if (os.path.isfile(_ENV_FILE) and os.access(_ENV_FILE, os.R_OK)) else None
)


class LLMConfig(BaseSettings):
    """Configuration for LLM service."""

    model_config = SettingsConfigDict(
        # In some restricted environments (e.g. sandbox/CI), `.env` may not be readable; this should not block app startup
        env_file=_ENV_FILE_FOR_SETTINGS,
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # Default model to use. The `openai/` prefix tells litellm to speak the
    # OpenAI protocol to `llm_api_base`.
    default_text_model: str = Field(
        default="openai/deepseek-flash-1017",
        description="Default text model for general purposes",
    )

    # Supported models (format: provider/model-name)
    supported_text_models: list[str] = Field(
        default=["openai/deepseek-flash-1017"],
        description="List of supported text models",
    )

    # OpenAI-compatible endpoint for text models. There is no default provider:
    # a call without both of these fails instead of reaching another service.
    llm_api_base: str = Field(
        default="",
        description="Base URL of the OpenAI-compatible text model provider",
    )
    llm_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="API key for the text model provider",
    )

    # Request timeout settings
    llm_timeout: int = Field(
        default=60, description="Timeout for LLM API calls in seconds"
    )

    # Temperature for deterministic outputs
    llm_temperature: float = Field(
        default=0.3,
        description="Temperature for LLM outputs (lower = more deterministic)",
    )

    # Max retries on failure
    llm_max_retries: int = Field(
        default=3, description="Maximum number of retries on API failure"
    )


# Global config instance
llm_config = LLMConfig()
