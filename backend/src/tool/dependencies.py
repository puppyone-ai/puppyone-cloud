from __future__ import annotations

from fastapi import Depends

from src.infra.supabase.dependencies import get_supabase_repository
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService
from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container
from src.tool.repository import ToolRepositorySupabase
from src.tool.service import ToolService

_tool_service: ToolService | None = None


def get_tool_service(
    authorization: AuthorizationService = Depends(get_authorization_service),
) -> ToolService:
    global _tool_service
    if _tool_service is None or _tool_service.authorization is not authorization:
        repo = ToolRepositorySupabase(get_supabase_repository())
        ops = build_worker_version_engine_container().product_operations()
        _tool_service = ToolService(
            repo=repo,
            ops=ops,
            authorization=authorization,
        )
    return _tool_service
