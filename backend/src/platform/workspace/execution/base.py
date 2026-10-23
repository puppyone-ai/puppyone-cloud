"""Sandbox abstract base class definitions."""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SandboxSession:
    """Sandbox session data"""
    sandbox: Any  # Concrete sandbox instance (E2B Sandbox or Docker container ID)
    readonly: bool
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


class SandboxBase(ABC):
    """
    Sandbox service abstract base class

    Defines the unified interface for sandbox services, supporting both E2B cloud sandbox and Docker local sandbox implementations.
    """

    @abstractmethod
    async def start(
        self,
        session_id: str,
        data: Any,
        readonly: bool,
        *,
        project_id: str | None = None,
    ) -> dict:
        """
        Create a sandbox session and preload a single JSON data into /workspace/data.json

        Args:
            session_id: Unique session identifier
            data: JSON data (will be written to /workspace/data.json)
            readonly: Whether to use read-only mode

        Returns:
            {"success": True} or {"success": False, "error": str}
        """
        ...

    @abstractmethod
    async def start_with_files(
        self,
        session_id: str,
        files: list,
        readonly: bool,
        s3_service: Optional[Any] = None,
        *,
        project_id: str | None = None,
    ) -> dict:
        """
        Create a sandbox session and preload multiple files

        Args:
            session_id: Unique session identifier
            files: List of SandboxFile, each containing path, content, s3_key
            readonly: Whether to use read-only mode
            s3_service: S3 service instance (for downloading S3 files)

        Returns:
            {"success": True} or {"success": False, "error": str}
            May include a "warnings" field listing failed files
        """
        ...

    @abstractmethod
    async def exec(self, session_id: str, command: str) -> dict:
        """
        Execute a command in the sandbox

        Args:
            session_id: Session identifier
            command: Bash command to execute

        Returns:
            {"success": True, "output": str} or {"success": False, "error": str}
        """
        ...

    @abstractmethod
    async def read(self, session_id: str) -> dict:
        """
        Read the contents of /workspace/data.json

        Args:
            session_id: Session identifier

        Returns:
            {"success": True, "data": dict} or {"success": False, "error": str}
        """
        ...

    @abstractmethod
    async def read_file(self, session_id: str, path: str, parse_json: bool = False) -> dict:
        """
        Read a file at the specified path in the sandbox

        Args:
            session_id: Session identifier
            path: File path (e.g. /workspace/myfile.json)
            parse_json: Whether to parse as JSON

        Returns:
            {"success": True, "content": str/dict} or {"success": False, "error": str}
        """
        ...

    @abstractmethod
    async def stop(self, session_id: str) -> dict:
        """
        Stop and clean up a sandbox session

        Args:
            session_id: Session identifier

        Returns:
            {"success": True}
        """
        ...

    @abstractmethod
    async def status(self, session_id: str) -> dict:
        """
        Get sandbox session status

        Args:
            session_id: Session identifier

        Returns:
            {"active": bool, ...} including other metadata
        """
        ...

    @abstractmethod
    async def stop_all(self) -> None:
        """Stop all sandbox sessions (used during service shutdown)"""
        ...
