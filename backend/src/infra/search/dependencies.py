from __future__ import annotations

from fastapi import Depends

from src.infra.chunking.repository import ChunkRepository
from src.infra.chunking.service import ChunkingService
from src.version_engine.bootstrap.dependencies import build_worker_version_engine_container
from src.infra.llm.embedding_service import EmbeddingService
from src.infra.search.service import SearchService
from src.infra.supabase.client import SupabaseClient
from src.infra.turbopuffer.service import TurbopufferSearchService
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService

_search_service: SearchService | None = None


def get_search_service(
    authorization: AuthorizationService = Depends(get_authorization_service),
) -> SearchService:
    global _search_service
    if _search_service is None or _search_service.authorization is not authorization:
        sb_client = SupabaseClient().get_client()
        chunk_repo = ChunkRepository(sb_client)
        ops = build_worker_version_engine_container().product_operations()

        _search_service = SearchService(
            ops=ops,
            chunk_repo=chunk_repo,
            authorization=authorization,
            chunking_service=ChunkingService(),
            embedding_service=EmbeddingService(),
            turbopuffer_service=TurbopufferSearchService(),
        )
    return _search_service
