"""
Supabase client module.

Provides a singleton wrapper for the Supabase client and CRUD operations for database tables.

Module structure:
- client: Supabase client singleton
- repository: Unified data access repository (Facade)
- projects: Project data access layer
- tables: Table data access layer
- dependencies: Dependency injection
- exceptions: Exception handling
- schemas: Data models (backward compatible)
"""

from src.infra.supabase.client import SupabaseClient
from src.infra.supabase.repository import SupabaseRepository
from src.infra.supabase.dependencies import (
    get_supabase_client,
    get_supabase_repository,
)
from src.infra.supabase.exceptions import (
    SupabaseException,
    SupabaseDuplicateKeyError,
    SupabaseNotFoundError,
    SupabaseForeignKeyError,
    handle_supabase_error,
)

# Re-exports from domain modules (backward compat)
from src.platform.project.supabase_repo import ProjectRepository
from src.content.table.supabase_repo import TableRepository

from src.platform.project.supabase_schemas import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
)
from src.content.table.supabase_schemas import (
    TableCreate,
    TableUpdate,
    TableResponse,
)

__all__ = [
    # Client and main repository
    "SupabaseClient",
    "SupabaseRepository",
    "get_supabase_client",
    "get_supabase_repository",
    # Sub-module Repositories (optional usage)
    "ProjectRepository",
    "TableRepository",
    # Exceptions
    "SupabaseException",
    "SupabaseDuplicateKeyError",
    "SupabaseNotFoundError",
    "SupabaseForeignKeyError",
    "handle_supabase_error",
    # Schema (backward compatible)
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectResponse",
    "TableCreate",
    "TableUpdate",
    "TableResponse",
]
