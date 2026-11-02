"""
LLM Service Dependencies

FastAPI dependency injection for LLM service.
"""

from src.infra.llm.service import LLMService


# Use global variables to store singletons instead of lru_cache
# This avoids cache issues during reload
_llm_service = None


def get_llm_service() -> LLMService:
    """
    Get LLM service instance (singleton).

    Returns:
        LLMService instance
    """
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
