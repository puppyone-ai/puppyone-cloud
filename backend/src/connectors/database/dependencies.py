"""DB Connector Dependency Injection"""

from fastapi import Depends
from src.infra.supabase.client import SupabaseClient
from src.connectors.database.repository import DBConnectionRepository
from src.connectors.database.service import DBConnectorService
from src.platform.authorization.dependencies import get_authorization_service
from src.platform.authorization.service import AuthorizationService


def _get_supabase_client() -> SupabaseClient:
    return SupabaseClient()


def get_db_connection_repository(
    supabase: SupabaseClient = Depends(_get_supabase_client),
) -> DBConnectionRepository:
    return DBConnectionRepository(supabase)


def get_db_connector_service(
    repo: DBConnectionRepository = Depends(get_db_connection_repository),
    authorization: AuthorizationService = Depends(get_authorization_service),
) -> DBConnectorService:
    return DBConnectorService(repo, authorization)
