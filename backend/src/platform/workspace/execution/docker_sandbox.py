"""Docker sandbox implementation."""

import asyncio
import json
import os
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Optional

from src.config import settings

from .base import SandboxBase, SandboxSession
from .store import ExecutionSession, ExecutionSessionStore, durable_execution_store

# Docker session timeout (seconds)
DEFAULT_DOCKER_SESSION_TIMEOUT = 600  # 10 minutes


@dataclass
class DockerSession(SandboxSession):
    """Docker sandbox session data"""
    container_id: str = ""
    temp_path: str = ""  # Temporary file or directory path


class DockerSandbox(SandboxBase):
    """
    Docker local sandbox implementation

    Uses Docker containers to run sandbox environments, supporting:
    - Single-file JSON data mounting
    - Multi-file mounting
    - Command execution
    - File reading
    """

    def __init__(
        self,
        session_timeout: float = DEFAULT_DOCKER_SESSION_TIMEOUT,
        session_store: ExecutionSessionStore | None = None,
    ):
        """
        Initialize Docker sandbox service

        Args:
            session_timeout: Session timeout in seconds, default 10 minutes
        """
        self._store = session_store or durable_execution_store()
        self._async_lock = asyncio.Lock()  # For async operation mutual exclusion
        self._session_timeout = session_timeout
        self._docker_available: Optional[bool] = None
        self._docker_check_time: float = 0  # Last check time
        self._docker_cache_ttl: float = 60.0  # Cache TTL (seconds)

    def _get_sandbox_temp_root(self) -> str:
        """
        Return the dedicated temporary directory for Docker sandbox.

        Falls back to the system default temporary directory when SANDBOX_TMPDIR
        is not configured, for compatibility with running the backend locally.
        """
        sandbox_tmpdir = (settings.SANDBOX_TMPDIR or "").strip()
        if sandbox_tmpdir:
            os.makedirs(sandbox_tmpdir, exist_ok=True)
            return sandbox_tmpdir
        return tempfile.gettempdir()

    def _create_temp_json_file(self, session_id: str) -> str:
        """Create a host-visible temporary JSON file for a single-file sandbox."""
        fd, temp_file_path = tempfile.mkstemp(
            prefix=f"sandbox-{session_id}-",
            suffix=".json",
            dir=self._get_sandbox_temp_root(),
        )
        os.close(fd)
        return temp_file_path

    def _create_temp_workspace_dir(self, session_id: str) -> str:
        """Create a host-visible temporary workspace directory for a multi-file sandbox."""
        return tempfile.mkdtemp(
            prefix=f"sandbox-{session_id}-",
            dir=self._get_sandbox_temp_root(),
        )

    async def _check_docker_available(self, force_recheck: bool = False) -> bool:
        """
        Check if Docker is available

        Args:
            force_recheck: Force recheck, ignoring cache

        Returns:
            Whether Docker is available
        """
        now = time.time()

        # Check if cache is valid
        # 1. If cache is True and not expired, return directly
        # 2. If cache is False, always recheck (Docker may have just started)
        # 3. If force_recheck is True, force recheck
        cache_expired = (now - self._docker_check_time) > self._docker_cache_ttl

        if not force_recheck and self._docker_available is True and not cache_expired:
            return True

        # If Docker was previously unavailable, or cache expired, or force check, re-detect
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            self._docker_available = proc.returncode == 0
        except Exception:
            self._docker_available = False

        self._docker_check_time = now
        return self._docker_available

    async def _run_docker_command(
        self,
        *args: str,
        timeout: float = 30.0
    ) -> tuple[int, str, str]:
        """
        Execute a Docker command

        Args:
            *args: Command arguments
            timeout: Timeout in seconds

        Returns:
            (return_code, stdout, stderr)
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )
            return (
                proc.returncode or 0,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace")
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return (-1, "", "Command timed out")
        except Exception as e:
            return (-1, "", str(e))

    async def _wait_for_container_ready(
        self,
        container_id: str,
        max_retries: int = 30,
        retry_interval: float = 1.0
    ) -> bool:
        """
        Wait for the container to be ready (verified by executing a simple command)

        Args:
            container_id: Container ID
            max_retries: Maximum number of retries
            retry_interval: Retry interval in seconds

        Returns:
            Whether the container is ready
        """
        for i in range(max_retries):
            # Try executing a simple command to verify the container is ready
            returncode, stdout, _ = await self._run_docker_command(
                "exec", container_id, "echo", "ready",
                timeout=5.0
            )
            if returncode == 0 and "ready" in stdout:
                return True

            if i < max_retries - 1:
                await asyncio.sleep(retry_interval)

        return False

    @staticmethod
    def _container_identity() -> str:
        """Use a non-root identity that can access host bind mounts.

        A non-root backend already owns its temporary files, so matching its
        numeric uid/gid avoids weakening host permissions. Root-run local
        backends and Docker Desktop use the image's fixed unprivileged user.
        """
        if os.name != "nt" and hasattr(os, "geteuid"):
            uid = os.geteuid()
            gid = os.getegid()
            if uid > 0:
                return f"{uid}:{gid}"
        return "65532:65532"

    @staticmethod
    def _prepare_bind_mount(path: str) -> None:
        """Transfer root-owned local temp files to the fixed container user."""
        if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0:
            return
        targets = [path]
        if os.path.isdir(path):
            targets.extend(
                os.path.join(root, name)
                for root, dirs, files in os.walk(path)
                for name in [*dirs, *files]
            )
        for target in targets:
            os.chown(target, 65532, 65532)

    def _isolation_args(self) -> list[str]:
        """Mandatory local Docker security contract; none is configurable off."""
        return [
            "--network=none",
            f"--user={self._container_identity()}",
            "--cap-drop=ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        ]

    async def _try_start_container(
        self,
        session_id: str,
        mount_args: list[str],
    ) -> tuple[bool, str, str]:
        """
        Attempt to start a Docker container

        Args:
            mount_args: List of mount arguments
        Returns:
            (success, container_id, error_message)
        """
        # Resource limits: prevent a single container from exhausting host resources
        resource_args = ["--memory=128m", "--cpus=0.5", "--pids-limit=100"]
        hardened_args = self._isolation_args()
        args = [
            "run", "-d", "--rm",
            "--label", f"puppyone.sandbox.session={session_id}",
            *resource_args,
            *hardened_args,
            *mount_args,
            "json-sandbox:3.19",
        ]
        returncode, stdout, stderr = await self._run_docker_command(*args, timeout=30.0)

        if returncode == 0:
            container_id = stdout.strip()
            if await self._wait_for_container_ready(container_id, max_retries=10):
                return (True, container_id, "")
            else:
                # Container not ready, clean up and fail
                await self._run_docker_command("stop", container_id, timeout=5.0)
                return (False, "", "Container started but not ready")

        return (False, "", f"Required image json-sandbox:3.19 failed to start: {stderr}")

    async def start(
        self,
        session_id: str,
        data: Any,
        readonly: bool = False,
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
        # Check if Docker is available
        if not await self._check_docker_available():
            return {
                "success": False,
                "error": "Docker is not available. Please ensure Docker is installed and running."
            }

        # If already exists, stop first (use async lock to protect the check-stop operation)
        async with self._async_lock:
            existing = self._store.get(session_id)
            if existing and not existing.resource_id:
                return {"success": False, "error": "Sandbox session is being started by another worker"}
            if existing:
                await self._stop_internal(session_id)
            now = time.time()
            claim = ExecutionSession(
                session_id=session_id, provider="docker", resource_id="",
                readonly=readonly, created_at=now, last_activity=now,
                project_id=project_id,
            )
            if not self._store.insert(claim):
                return {"success": False, "error": "Sandbox session is being started by another worker"}

        # Create temporary JSON file
        temp_file_path = self._create_temp_json_file(session_id)

        try:
            json_content = json.dumps(data, ensure_ascii=False, indent=2)
            with open(temp_file_path, "w", encoding="utf-8") as f:
                f.write(json_content)
            self._prepare_bind_mount(temp_file_path)
        except Exception as e:
            self._store.delete(session_id)
            return {"success": False, "error": f"Failed to create temp file: {e}"}

        # Build mount arguments
        mount_option = f"{temp_file_path}:/workspace/data.json"
        if readonly:
            mount_option += ":ro"
        mount_args = ["-v", mount_option]

        # Start container
        success, container_id, error = await self._try_start_container(session_id, mount_args)

        if not success:
            self._store.delete(session_id)
            # Clean up temporary file
            try:
                os.unlink(temp_file_path)
            except Exception:
                pass
            return {"success": False, "error": error}

        try:
            claim.resource_id = container_id
            claim.temp_path = temp_file_path
            claim.last_activity = time.time()
            self._store.put(claim)
        except Exception:
            await self._run_docker_command("stop", container_id, timeout=5.0)
            if os.path.isfile(temp_file_path):
                os.unlink(temp_file_path)
            self._store.delete(session_id)
            raise

        print(f"[DockerSandbox] Started session {session_id}, container: {container_id[:12]}, readonly: {readonly}")
        return {"success": True}

    async def start_with_files(
        self,
        session_id: str,
        files: list,
        readonly: bool = False,
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

        # Check if Docker is available
        if not await self._check_docker_available():
            return {
                "success": False,
                "error": "Docker is not available. Please ensure Docker is installed and running."
            }

        # If already exists, stop first (use async lock to protect the check-stop operation)
        async with self._async_lock:
            existing = self._store.get(session_id)
            if existing and not existing.resource_id:
                return {"success": False, "error": "Sandbox session is being started by another worker"}
            if existing:
                await self._stop_internal(session_id)
            now = time.time()
            claim = ExecutionSession(
                session_id=session_id, provider="docker", resource_id="",
                readonly=readonly, created_at=now, last_activity=now,
                project_id=project_id,
            )
            if not self._store.insert(claim):
                return {"success": False, "error": "Sandbox session is being started by another worker"}

        # Create temporary directory to store all files
        temp_dir = ""
        try:
            temp_dir = self._create_temp_workspace_dir(session_id)
            workspace_dir = os.path.join(temp_dir, "workspace")
            os.makedirs(workspace_dir, exist_ok=True)

            # Use the dedicated Docker file preparation function, large files are streamed directly to disk
            from .file_utils import prepare_files_for_docker_sandbox
            written_paths, all_failures = await prepare_files_for_docker_sandbox(
                files, workspace_dir, s3_service
            )
            self._prepare_bind_mount(workspace_dir)
        except Exception as exc:
            self._store.delete(session_id)
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            return {"success": False, "error": f"Failed to prepare sandbox files: {exc}"}

        # Build mount arguments
        mount_option = f"{workspace_dir}:/workspace"
        if readonly:
            mount_option += ":ro"
        mount_args = ["-v", mount_option]

        # Start container
        success, container_id, error = await self._try_start_container(session_id, mount_args)

        if not success:
            self._store.delete(session_id)
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
            return {"success": False, "error": error}

        try:
            claim.resource_id = container_id
            claim.temp_path = temp_dir
            claim.last_activity = time.time()
            self._store.put(claim)
        except Exception:
            await self._run_docker_command("stop", container_id, timeout=5.0)
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._store.delete(session_id)
            raise

        print(f"[DockerSandbox] Started session {session_id} with {len(written_paths)} files written, container: {container_id[:12]}, readonly: {readonly}")

        result: dict[str, Any] = {"success": True}
        if all_failures:
            result["warnings"] = all_failures
        return result

    async def exec(self, session_id: str, command: str) -> dict:
        """
        Execute a command in the sandbox

        Args:
            session_id: Session identifier
            command: Bash command to execute

        Returns:
            {"success": True, "output": str} or {"success": False, "error": str}
        """
        session = self._store.get(session_id)
        if not session or session.provider != "docker":
            return {
                "success": False,
                "error": "Sandbox session not found. Call start first."
            }

        # Update last activity time
        session.last_activity = time.time()
        self._store.put(session)

        # Security notes:
        # 1. _run_docker_command uses asyncio.subprocess_exec, bypassing the host shell,
        #    so command is passed as a single argument to sh -c inside the container, no host-level injection risk
        # 2. Executing arbitrary commands inside the container is the sandbox's design purpose, no need to restrict at this level
        # 3. The container itself provides isolation, limiting the potential scope of damage

        returncode, stdout, stderr = await self._run_docker_command(
            "exec", session.resource_id,
            "sh", "-c", command,
            timeout=30.0
        )

        if returncode == 0:
            return {"success": True, "output": stdout}
        else:
            # Command execution failed, uniformly return success=False
            # Also provide output and exit_code for the caller to get detailed info
            output = stdout + stderr
            return {
                "success": False,
                "error": f"Command failed with exit code {returncode}",
                "output": output,
                "exit_code": returncode
            }

    async def read(self, session_id: str) -> dict:
        """
        Read the contents of /workspace/data.json

        Args:
            session_id: Session identifier

        Returns:
            {"success": True, "data": dict} or {"success": False, "error": str}
        """
        result = await self.exec(session_id, "cat /workspace/data.json")

        if not result.get("success"):
            return {"success": False, "error": result.get("error", "Failed to read file")}

        try:
            data = json.loads(result.get("output", ""))
            return {"success": True, "data": data}
        except json.JSONDecodeError:
            return {"success": False, "error": "Failed to parse JSON"}

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
        # Use shlex.quote to prevent path injection
        safe_path = shlex.quote(path)
        result = await self.exec(session_id, f"cat {safe_path}")

        if not result.get("success"):
            return {"success": False, "error": result.get("error", f"Failed to read {path}")}

        content = result.get("output", "")

        if parse_json:
            try:
                data = json.loads(content)
                return {"success": True, "content": data}
            except json.JSONDecodeError:
                return {"success": False, "error": f"Failed to parse JSON from {path}"}

        return {"success": True, "content": content}

    async def _stop_internal(self, session_id: str) -> bool:
        """
        Internal stop method, does not acquire the async lock

        Args:
            session_id: Session identifier

        Returns:
            Whether the session was successfully stopped (whether it existed)
        """
        session = self._store.get(session_id)
        if not session or session.provider != "docker":
            return False  # Already does not exist

        # Stop container
        try:
            returncode, _stdout, stderr = await self._run_docker_command(
                "stop", session.resource_id, timeout=10.0
            )
            if returncode != 0 and "no such container" not in stderr.lower():
                print(f"[DockerSandbox] Failed to stop {session_id}: {stderr}")
                return False
        except Exception as e:
            print(f"[DockerSandbox] Error stopping container {session_id}: {e}")
            return False

        # Clean up temporary files/directories
        if session.temp_path:
            try:
                if os.path.isdir(session.temp_path):
                    shutil.rmtree(session.temp_path)
                elif os.path.isfile(session.temp_path):
                    os.unlink(session.temp_path)
            except Exception as e:
                print(f"[DockerSandbox] Error cleaning temp path {session.temp_path}: {e}")

        self._store.delete(session_id)

        print(f"[DockerSandbox] Stopped session {session_id}")
        return True

    async def stop(self, session_id: str) -> dict:
        """
        Stop and clean up a sandbox session

        Args:
            session_id: Session identifier

        Returns:
            {"success": True}
        """
        async with self._async_lock:
            await self._stop_internal(session_id)
        return {"success": True}

    async def status(self, session_id: str) -> dict:
        """
        Get sandbox session status

        Args:
            session_id: Session identifier

        Returns:
            {"active": bool, ...} including other metadata
        """
        session = self._store.get(session_id)
        if not session or session.provider != "docker":
            return {"active": False}

        return {
            "active": True,
            "container_id": session.resource_id[:12] if session.resource_id else None,
            "readonly": session.readonly,
            "created_at": session.created_at,
            "last_activity": session.last_activity,
        }

    async def stop_all(self) -> None:
        """Stop all sandbox sessions (used during service shutdown)"""
        async with self._async_lock:
            for session in self._store.list_provider("docker"):
                await self._stop_internal(session.session_id)

        print("[DockerSandbox] All sessions stopped")
