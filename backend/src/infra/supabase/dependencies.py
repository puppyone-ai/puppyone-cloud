"""
Supabase dependency injection.

Provides the FastAPI dependency for obtaining the Supabase client.
"""

from supabase import Client
from src.infra.supabase.client import SupabaseClient


# Use global variables to store singletons instead of lru_cache
# This avoids cache issues during reload
_supabase_client = None


def get_supabase_client() -> Client:
    """
    Get Supabase client instance (singleton).

    Returns:
        Supabase Client instance
    """
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = SupabaseClient().client
    return _supabase_client
