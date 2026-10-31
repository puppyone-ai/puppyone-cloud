"""Version Engine bootstrap for processes without a request."""

from __future__ import annotations

from src.version_engine.bootstrap.container import (
    VersionEngineContainer,
    build_version_engine_container,
)


def build_worker_version_engine_container(
    *,
    probe: bool = False,
) -> VersionEngineContainer:
    """Explicit bootstrap for scheduler jobs, ARQ workers, and CLI scripts.

    Build once at worker boot and inject into jobs. This function creates
    storage resources; it must not be used as a request dependency.
    """

    return build_version_engine_container(probe=probe)
