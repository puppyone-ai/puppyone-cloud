"""Workspace errors shared by HTTP, Agent and SSH callers."""


class WorkspaceError(Exception):
    def __init__(self, status_code: int, detail: dict | str):
        self.status_code = status_code
        self.detail = detail
        self.code = detail["code"] if isinstance(detail, dict) else "workspace_error"
        super().__init__(self.code)


def access_failure(error: Exception) -> bool:
    """Recognize authorization/billing failures without importing a wire adapter."""
    return isinstance(error, PermissionError) or getattr(error, "status_code", None) in {402, 403}
