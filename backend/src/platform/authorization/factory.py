"""Construction helpers for non-FastAPI call sites.

Routers should prefer dependency injection. Workers and legacy service classes
that are not dependency-injected use this factory while still resolving through
the one canonical PDP.
"""

from __future__ import annotations

from typing import Any

from src.platform.authorization.repository import AuthorizationRepository
from src.platform.authorization.service import AuthorizationService


def build_authorization_service(
    supabase_client: Any | None = None,
) -> AuthorizationService:
    return AuthorizationService(AuthorizationRepository(supabase_client))
