from __future__ import annotations

from fastapi import Depends

from src.content.table.dependencies import get_table_service
from src.content.table.service import TableService
from src.context_publish.repository import ContextPublishRepositorySupabase
from src.context_publish.service import ContextPublishService
from src.infra.supabase.dependencies import get_supabase_repository
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService

_publish_service: ContextPublishService | None = None


def get_context_publish_service(
    table_service: TableService = Depends(get_table_service),
    authorization: AuthorizationService = Depends(get_authorization_service),
) -> ContextPublishService:
    global _publish_service
    if (
        _publish_service is None
        or _publish_service.table_service is not table_service
        or _publish_service.authorization is not authorization
    ):
        repo = ContextPublishRepositorySupabase(get_supabase_repository())
        _publish_service = ContextPublishService(
            repo=repo,
            table_service=table_service,
            authorization=authorization,
        )
    return _publish_service
