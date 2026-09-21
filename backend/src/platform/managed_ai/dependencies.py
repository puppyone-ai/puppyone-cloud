"""Composition root: the only place selecting concrete provider implementations."""

import os

from fastapi import Depends

from src.config import settings
from src.platform.billing.gateway import PuppyPayGateway, get_billing_gateway
from src.platform.managed_ai.providers.openrouter import OpenRouterProvider
from src.platform.managed_ai.providers.registry import ProviderRegistry
from src.platform.managed_ai.service import ManagedAIService


def get_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(
        {
            ("openrouter", "managed-default"): OpenRouterProvider(
                key=settings.MANAGED_AI_OPENROUTER_KEY or os.environ.get("OPENROUTER_API_KEY", "")
            ),
        }
    )


def get_inference_service(
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
    providers: ProviderRegistry = Depends(get_provider_registry),
) -> ManagedAIService:
    return ManagedAIService(gateway, providers=providers)
