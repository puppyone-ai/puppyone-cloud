"""Workspace errors shared by HTTP, Agent and SSH callers."""

from src.platform.sandbox import WorkerLost, WorkspaceDiskLost, WorkspaceUnavailable


class WorkspaceError(Exception):
    def __init__(self, status_code: int, detail: dict | str):
        self.status_code = status_code
        self.detail = detail
        self.code = detail["code"] if isinstance(detail, dict) else "workspace_error"
        super().__init__(self.code)


def access_failure(error: Exception) -> bool:
    """Recognize authorization/billing failures without importing a wire adapter."""
    return isinstance(error, PermissionError) or getattr(error, "status_code", None) in {402, 403}


def context_changed(error: Exception) -> bool:
    """The authorization revision moved between reading facts and acting on them."""
    return getattr(error, "code", None) == "P0001" and "agent_context_changed" in str(
        getattr(error, "message", None) or error
    )


# Fenced SQL refusals an opening can meet: (status, code, retryable). Each says
# the durable workspace moved on, so the same request can be decided again.
_SQL_REFUSALS = {
    "workbench_fenced": (409, "workbench_operation_replaced", True),
    "workbench_not_running": (409, "workbench_not_running", True),
    "workbench_unavailable": (409, "workbench_unavailable", False),
}


def _failure(status: int, code: str, retryable: bool) -> WorkspaceError:
    return WorkspaceError(status, {"code": code, "retryable": retryable})


def opening_failure(error: Exception) -> WorkspaceError | None:
    """What an interrupted opening tells its caller.

    Opening a workspace crosses the database, the provider and a connection to
    the machine; any of them can refuse or drop part way. Each such refusal has
    a stable code and says whether the same request can later succeed, so the
    caller never sees an anonymous server error for an ordinary interruption.
    Returns None for an error that already carries its own HTTP contract, and
    for one nobody has classified: that stays a server error and is logged.
    """
    if isinstance(error, WorkspaceError) or hasattr(error, "status_code"):
        return None
    if isinstance(error, WorkspaceUnavailable):
        return _failure(503, error.code, error.retryable)
    if isinstance(error, WorkspaceDiskLost):
        return _failure(503, "workbench_disk_lost", True)
    if isinstance(error, WorkerLost):
        # Compute is gone and the disk is the workspace; reopening recreates it.
        return _failure(503, "workbench_preparing", True)
    if isinstance(error, ConnectionError):
        return _failure(503, "workbench_connection_unavailable", True)
    if isinstance(error, TimeoutError):
        return _failure(503, "workbench_connection_timeout", True)
    if isinstance(error, PermissionError):
        return _failure(403, "workbench_access_denied", False)
    if context_changed(error):
        return _failure(409, "workbench_context_changed", True)
    message = str(getattr(error, "message", None) or "")
    if message in _SQL_REFUSALS:
        return _failure(*_SQL_REFUSALS[message])
    return None
