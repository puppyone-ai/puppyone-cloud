"""Deployment defaults select new resources; persisted bindings select existing ones."""

from src.config import settings
from .port import RuntimeProvider


def default_provider() -> str:
    selected = settings.CLOUD_AGENT_SANDBOX_PROVIDER
    if selected == "auto":
        selected = settings.SANDBOX_TYPE
    if selected == "auto":
        selected = "e2b" if settings.E2B_API_KEY else "docker"
    if selected not in {"docker", "e2b", "fly"}:
        raise ValueError(f"Unsupported Agent sandbox provider: {selected}")
    return selected


def runtime_provider(name: str | None = None) -> RuntimeProvider:
    selected = name if name is not None else default_provider()
    if selected == "docker":
        from .docker import DockerRuntimeProvider

        return DockerRuntimeProvider()
    if selected == "e2b":
        from .e2b import E2BRuntimeProvider

        return E2BRuntimeProvider()
    if selected == "fly":
        from .fly import FlyRuntimeProvider

        return FlyRuntimeProvider()
    raise ValueError(f"Unsupported Agent sandbox provider: {selected}")
