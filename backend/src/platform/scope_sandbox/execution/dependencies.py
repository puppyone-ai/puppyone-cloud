from src.platform.scope_sandbox.execution.service import SandboxService

_sandbox_service = None


def get_sandbox_service() -> SandboxService:
    global _sandbox_service
    if _sandbox_service is None:
        _sandbox_service = SandboxService()
    return _sandbox_service
