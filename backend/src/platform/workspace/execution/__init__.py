"""Ephemeral execution mode of the unified scope-sandbox subsystem."""

from .base import SandboxBase, SandboxSession
from .service import SandboxService, get_sandbox_type
from .e2b_sandbox import E2BSandbox
from .docker_sandbox import DockerSandbox, DockerSession

__all__ = [
    # Abstract base classes
    "SandboxBase",
    "SandboxSession",
    # Unified service
    "SandboxService",
    "get_sandbox_type",
    # Concrete implementations
    "E2BSandbox",
    "DockerSandbox",
    "DockerSession",
]
