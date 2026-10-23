from __future__ import annotations

from fastapi import Depends

from src.file_operations.operation_adapter import ProductOperationAdapter
from src.infra.supabase.dependencies import get_supabase_repository
from src.infra.supabase.repository import SupabaseRepository
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService
from src.tool.repository import ToolRepositorySupabase
from src.tool.service import ToolService
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter


def get_tool_service(
    authorization: AuthorizationService = Depends(get_authorization_service),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
    repository: SupabaseRepository = Depends(get_supabase_repository),
) -> ToolService:
    return ToolService(
        repo=ToolRepositorySupabase(repository),
        ops=ops,
        authorization=authorization,
    )
