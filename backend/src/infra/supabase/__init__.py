"""
Supabase client module.

Provides the Supabase client singleton, its dependency injection and error
handling. Domain repositories live with their domains.
"""

from src.infra.supabase.client import SupabaseClient
from src.infra.supabase.dependencies import get_supabase_client
from src.infra.supabase.exceptions import (
    SupabaseDuplicateKeyError,
    SupabaseException,
    SupabaseForeignKeyError,
    SupabaseNotFoundError,
    handle_supabase_error,
)

__all__ = [
    "SupabaseClient",
    "SupabaseDuplicateKeyError",
    "SupabaseException",
    "SupabaseForeignKeyError",
    "SupabaseNotFoundError",
    "get_supabase_client",
    "handle_supabase_error",
]
