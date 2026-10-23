from __future__ import annotations

from fastapi import Depends
from supabase import Client

from src.file_operations.operation_adapter import ProductOperationAdapter
from src.infra.chunking.repository import ChunkRepository
from src.infra.chunking.service import ChunkingService
from src.infra.llm.dependencies import get_embedding_service
from src.infra.llm.embedding_service import EmbeddingService
from src.infra.search.service import SearchService
from src.infra.supabase.dependencies import get_supabase_client
from src.infra.turbopuffer.dependencies import get_turbopuffer_search_service
from src.infra.turbopuffer.service import TurbopufferSearchService
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter


def get_search_service(
    authorization: AuthorizationService = Depends(get_authorization_service),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
    client: Client = Depends(get_supabase_client),
    embedding: EmbeddingService = Depends(get_embedding_service),
    vectors: TurbopufferSearchService = Depends(get_turbopuffer_search_service),
) -> SearchService:
    return SearchService(
        ops=ops,
        chunk_repo=ChunkRepository(client),
        authorization=authorization,
        chunking_service=ChunkingService(),
        embedding_service=embedding,
        turbopuffer_service=vectors,
    )
