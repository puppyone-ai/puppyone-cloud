"""Global test configuration — set required env vars for unit test collection.

Integration tests that need live services (Supabase, S3, MineRU) should
use pytest marks and skip appropriately.
"""

import os
from pathlib import Path

# Set minimal env vars so test modules can import without crashing.
# These are dummy values — tests that need real services should mock them.
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key-for-unit-tests")
os.environ.setdefault("INTERNAL_API_SECRET", "test-secret-for-unit-tests")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SKIP_AUTH", "true")
# Tests opt into managed inference explicitly; a developer's .env must not
# combine a paid gateway with the collection-time test authentication bypass.
os.environ.setdefault("MANAGED_AI_ENABLED", "false")
# Unit tests must never write transport caches into the developer's home.
# PID isolation also prevents concurrent pytest workers from sharing locks or
# stale refs while still allowing cache behavior to be exercised.
_configured_git_cache = "PUPPYONE_GIT_VIEW_CACHE_DIR" in os.environ
os.environ.setdefault(
    "PUPPYONE_GIT_VIEW_CACHE_DIR",
    f"/tmp/puppyone-git-view-cache-{os.getpid()}",
)

# Materialize Settings while the safe test-only cache root is present.  The
# transport adapter intentionally gives a live environment override priority
# over Settings in production, so leaving our synthetic override in os.environ
# would make tests unable to exercise/override the Settings branch.  Preserve a
# caller-supplied environment value, but remove only the value created above.
from src.config import settings as _settings  # noqa: E402,F401

if not _configured_git_cache:
    _settings.GIT_VIEW_CACHE_DIR = Path(
        f"/tmp/puppyone-git-view-cache-{os.getpid()}"
    )
    os.environ.pop("PUPPYONE_GIT_VIEW_CACHE_DIR", None)
