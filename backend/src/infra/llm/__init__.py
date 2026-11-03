"""
LLM Service Module

Provides a unified interface for interacting with text models via litellm.
"""

from src.infra.llm.dependencies import get_llm_service
from src.infra.llm.service import LLMService
from src.infra.llm.schemas import TextModelRequest, TextModelResponse

__all__ = [
    "LLMService",
    "get_llm_service",
    "TextModelRequest",
    "TextModelResponse",
]
