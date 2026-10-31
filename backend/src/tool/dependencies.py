from __future__ import annotations

from fastapi import Depends
from supabase import Client

from src.file_operations.operation_adapter import ProductOperationAdapter
from src.infra.supabase.dependencies import get_supabase_client
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService
from src.tool.repository import ToolRepositorySupabase
from src.tool.service import ToolService
from src.tool.supabase_repo import ToolRepository
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter


def get_tool_service(
    authorization: AuthorizationService = Depends(get_authorization_service),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
    client: Client = Depends(get_supabase_client),
) -> ToolService:
    return ToolService(
        repo=ToolRepositorySupabase(ToolRepository(client)),
        ops=ops,
        authorization=authorization,
    )
