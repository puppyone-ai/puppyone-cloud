"""The runtime handshake is shared by every sandbox provider."""

from .worker_port import WorkerDisconnected

RUNTIME_PROTOCOL = 1
FILE_PROTOCOL = 1
REQUIRED_CAPABILITIES = {"workspace_lifecycle", "native_files", "file_watch"}


class WorkspaceUnavailable(WorkerDisconnected):
    def __init__(self, code, *, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def validate_runtime(value):
    import re

    if (
        not isinstance(value, dict)
        or value.get("runtime_protocol") != RUNTIME_PROTOCOL
        or value.get("file_protocol") != FILE_PROTOCOL
        or not isinstance(value.get("capabilities"), list)
        or not all(isinstance(item, str) for item in value["capabilities"])
        or not REQUIRED_CAPABILITIES.issubset(value["capabilities"])
        or not isinstance(value.get("build_id"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", value.get("build_id", ""))
    ):
        raise WorkspaceUnavailable("workbench_protocol_mismatch")
    return value
