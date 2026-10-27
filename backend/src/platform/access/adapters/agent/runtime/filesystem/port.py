"""Agent filesystem capability; its contract is owned by Workspace."""

from src.platform.workspace.filesystem.port import (
    FileServiceChannel, FilesystemChannel, FilesystemError, FilesystemPort, Row,
)

__all__ = ["FileServiceChannel", "FilesystemChannel", "FilesystemError", "FilesystemPort", "Row"]
