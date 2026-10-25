"""Deployment defaults select new resources; persisted bindings select existing ones."""

from importlib import import_module

from src.config import settings

from .port import RuntimeProvider

PROVIDERS = {
    "docker": (".providers.docker", "DockerRuntimeProvider"),
    "e2b": (".providers.e2b", "E2BRuntimeProvider"),
    "fly": (".providers.fly", "FlyRuntimeProvider"),
    "sprites": (".providers.sprites", "SpritesRuntimeProvider"),
}


def default_provider() -> str:
    selected = settings.CLOUD_AGENT_SANDBOX_PROVIDER
    if selected == "auto":
        selected = settings.SANDBOX_TYPE
    if selected == "auto":
        selected = "e2b" if settings.E2B_API_KEY else "docker"
    if selected not in PROVIDERS:
        raise ValueError(f"Unsupported Agent sandbox provider: {selected}")
    return selected


def workspace_providers(default: str | None = None) -> tuple[str, ...]:
    """Default for new workspaces plus explicitly retained providers for old bindings."""
    selected = default if default is not None else default_provider()
    existing = [
        name.strip()
        for name in settings.CLOUD_AGENT_EXISTING_SANDBOX_PROVIDERS.split(",")
        if name.strip()
    ]
    if any(name not in PROVIDERS for name in existing):
        raise ValueError("Unsupported existing workspace provider")
    if settings.APP_ENV in {"staging", "production"} and "docker" in {selected, *existing}:
        raise ValueError("Hosted workspaces cannot use Docker")
    return tuple(dict.fromkeys([selected, *existing]))


def runtime_provider(name: str | None = None) -> RuntimeProvider:
    selected = name if name is not None else default_provider()
    if selected not in PROVIDERS:
        raise ValueError(f"Unsupported Agent sandbox provider: {selected}")
    module, factory = PROVIDERS[selected]
    return getattr(import_module(module, package=__package__), factory)()


async def close_provider_transports():
    """Application shutdown releases shared connections, never provider compute."""
    import sys

    for module, factory in PROVIDERS.values():
        loaded = sys.modules.get(__package__ + module if module.startswith(".") else module)
        if loaded is not None:
            close = getattr(getattr(loaded, factory), "shutdown_transports", None)
            if close is not None:
                await close()
