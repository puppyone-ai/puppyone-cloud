"""Load and cache the active repository policy from built-in profiles or an override file."""

from __future__ import annotations

import threading
import tomllib
from pathlib import Path

from src.config import settings
from src.platform.repository_policy.model import RepositoryPolicy

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
BUILTIN_PROFILES = frozenset({"vm-only", "open"})

_cache_lock = threading.Lock()
_cached_key: tuple[str, str] | None = None
_cached_policy: RepositoryPolicy | None = None


def load_policy_from_path(path: Path | str) -> RepositoryPolicy:
    """Parse and strictly validate a TOML policy file."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"Repository policy file not found: {target}")
    raw = tomllib.loads(target.read_text(encoding="utf-8"))
    return RepositoryPolicy.model_validate(raw)


def load_named_profile(name: str) -> RepositoryPolicy:
    """Load one of the shipped policy profiles (vm-only, open)."""
    normalized = (name or "").strip()
    if normalized not in BUILTIN_PROFILES:
        raise ValueError(
            f"Unknown REPOSITORY_POLICY profile: {name!r} "
            f"(expected one of {sorted(BUILTIN_PROFILES)})"
        )
    return load_policy_from_path(PROFILES_DIR / f"{normalized}.toml")


def _active_key() -> tuple[str, str]:
    return (
        (settings.REPOSITORY_POLICY or "").strip(),
        (settings.REPOSITORY_POLICY_FILE or "").strip(),
    )


def load_repository_policy(*, force_reload: bool = False) -> RepositoryPolicy:
    """Load and validate the configured repository policy once per active config."""
    global _cached_key, _cached_policy
    key = _active_key()
    with _cache_lock:
        if not force_reload and _cached_policy is not None and _cached_key == key:
            return _cached_policy
        profile_name, file_override = key
        policy = (
            load_policy_from_path(file_override)
            if file_override
            else load_named_profile(profile_name)
        )
        _cached_key = key
        _cached_policy = policy
        return policy


def get_repository_policy() -> RepositoryPolicy:
    """Return the active cached repository policy."""
    return load_repository_policy(force_reload=False)


def reset_policy_cache() -> None:
    """Clear the cached repository policy (used by tests)."""
    global _cached_key, _cached_policy
    with _cache_lock:
        _cached_key = None
        _cached_policy = None
