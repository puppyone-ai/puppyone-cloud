"""E2B cloud sandbox implementation."""

import asyncio
import inspect
import json
import os
import shlex
import time
from typing import Any, Callable, Optional

from .base import SandboxBase
from .store import (
    ExecutionSession,
    ExecutionSessionStore,
    durable_execution_store,
)

# Default sandbox session timeout (seconds)
DEFAULT_SESSION_TIMEOUT = 1800  # 30 minutes


class E2BSandbox(SandboxBase):
    """
    E2B cloud sandbox implementation

    Uses the e2b-code-interpreter SDK to provide a cloud-based isolated code execution environment.
    """

    def __init__(
        self,
        sandbox_factory: Optional[Callable[[], Any]] = None,
        sandbox_connector: Optional[Callable[[str], Any]] = None,
        session_store: ExecutionSessionStore | None = None,
        session_timeout: float = DEFAULT_SESSION_TIMEOUT,
    ):
        """
        Initialize E2B sandbox service

        Args:
            sandbox_factory: Sandbox factory function (mainly for testing)
            session_timeout: Session timeout in seconds
        """
        self._sandbox_factory = sandbox_factory or _default_e2b_factory
        self._sandbox_connector = sandbox_connector or _default_e2b_connector
        self._store = session_store or durable_execution_store()
        self._session_timeout = session_timeout

    async def _resolve(self, session_id: str):
        session = self._store.get(session_id)
        if not session or session.provider != "e2b":
            return None, None
        try:
            sandbox = await _call_maybe_async(
                self._sandbox_connector, session.resource_id
            )
        except Exception:
            return None, None
        session.last_activity = time.time()
        self._store.put(session)
        return session, sandbox

    async def start(
        self,
        session_id: str,
        data: Any,
        readonly: bool,
        *,
        project_id: str | None = None,
    ) -> dict:
        """Create a sandbox session and preload data into /workspace/data.json"""
        if data is None:
            return {"success": False, "error": "data is required"}

        await self.stop(session_id)

        now = time.time()
        claim = ExecutionSession(
            session_id=session_id, provider="e2b", resource_id="",
            readonly=bool(readonly), created_at=now, last_activity=now,
            project_id=project_id,
        )
        if not self._store.insert(claim):
            return {"success": False, "error": "Sandbox session is being started by another worker"}

        # Create a fresh sandbox instance for this session.
        try:
            sandbox = await _call_maybe_async(self._sandbox_factory)
        except Exception as e:
            self._store.delete(session_id)
            msg = str(e)
            # e2b-code-interpreter raises this type of error when authentication is not configured:
            # "Could not resolve authentication method. Expected either api_key or auth_token ..."
            if "Could not resolve authentication method" in msg:
                hint = (
                    "E2B sandbox auth is not configured.\n"
                    "- Set `E2B_API_KEY` in `backend/.env` (or export it) and restart the backend, OR\n"
                    "- Remove bash access from the Agent configuration (Agent Settings → Data Access).\n"
                    f"- Detected E2B_API_KEY={'set' if os.getenv('E2B_API_KEY') else 'missing'}"
                )
                msg = f"{hint}\nOriginal error: {msg}"
            return {"success": False, "error": msg}

        resource_id = str(getattr(sandbox, "id", "") or getattr(sandbox, "sandbox_id", ""))
        if not resource_id:
            await _close_e2b(sandbox)
            self._store.delete(session_id)
            return {"success": False, "error": "E2B provider returned no sandbox id"}
        claim.resource_id = resource_id
        claim.last_activity = time.time()
        self._store.put(claim)

        # Persist JSON data so bash tools can operate on it.
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            await _call_maybe_async(sandbox.files.write, "/workspace/data.json", payload)
        except Exception as exc:
            await self.stop(session_id)
            return {"success": False, "error": f"Failed to initialize E2B workspace: {exc}"}
        return {"success": True}

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
        """
        from .file_utils import prepare_files_for_sandbox

        await self.stop(session_id)

        now = time.time()
        claim = ExecutionSession(
            session_id=session_id, provider="e2b", resource_id="",
            readonly=bool(readonly), created_at=now, last_activity=now,
            project_id=project_id,
        )
        if not self._store.insert(claim):
            return {"success": False, "error": "Sandbox session is being started by another worker"}

        # Create a fresh sandbox instance
        try:
            sandbox = await _call_maybe_async(self._sandbox_factory)
        except Exception as e:
            self._store.delete(session_id)
            msg = str(e)
            if "Could not resolve authentication method" in msg:
                hint = (
                    "E2B sandbox auth is not configured.\n"
                    "- Set `E2B_API_KEY` in `backend/.env` (or export it) and restart the backend, OR\n"
                    "- Remove bash access from the Agent configuration (Agent Settings → Data Access).\n"
                    f"- Detected E2B_API_KEY={'set' if os.getenv('E2B_API_KEY') else 'missing'}"
                )
                msg = f"{hint}\nOriginal error: {msg}"
            return {"success": False, "error": msg}

        resource_id = str(getattr(sandbox, "id", "") or getattr(sandbox, "sandbox_id", ""))
        if not resource_id:
            await _close_e2b(sandbox)
            self._store.delete(session_id)
            return {"success": False, "error": "E2B provider returned no sandbox id"}
        claim.resource_id = resource_id
        claim.last_activity = time.time()
        self._store.put(claim)

        # Download all files in parallel
        prepared_files, failed_files = await prepare_files_for_sandbox(files, s3_service)

        # Create directories and write files to the sandbox
        created_dirs: set[str] = set()
        write_failures: list[dict] = []

        # First ensure /workspace directory exists
        # E2B sandbox runs as a regular user, needs sudo to create folders in the root directory
        try:
            mkdir_result = await _call_maybe_async(
                sandbox.commands.run,
                "sudo mkdir -p /workspace && sudo chmod 777 /workspace"
            )
            exit_code = getattr(mkdir_result, "exit_code", None)
            if exit_code is not None and exit_code != 0:
                stderr = getattr(mkdir_result, "stderr", "")
                print(f"[E2BSandbox] Warning: Failed to create /workspace directory with sudo: exit_code={exit_code}, stderr={stderr}")
                # Try using user directory as fallback
                fallback_result = await _call_maybe_async(
                    sandbox.commands.run,
                    "mkdir -p ~/workspace && sudo ln -sf ~/workspace /workspace 2>/dev/null || true"
                )
                fallback_code = getattr(fallback_result, "exit_code", None)
                if fallback_code == 0:
                    print("[E2BSandbox] Created /workspace via symlink to ~/workspace")
                else:
                    print("[E2BSandbox] Fallback also failed, continuing anyway...")
            else:
                print("[E2BSandbox] Created /workspace directory with sudo")
        except Exception as e:
            print(f"[E2BSandbox] Error creating /workspace directory: {e}")

        for f in prepared_files:
            path = f["path"]
            content = f["content"]

            # Security check: prevent path traversal attacks
            # Normalize path using posixpath (not os.path which uses backslashes on Windows)
            import posixpath
            normalized_path = posixpath.normpath(path)
            # Only allow paths under /workspace
            if not normalized_path.startswith("/workspace/") and normalized_path != "/workspace":
                # If path doesn't start with /workspace, automatically add the prefix
                if normalized_path.startswith("/"):
                    normalized_path = "/workspace" + normalized_path
                else:
                    normalized_path = "/workspace/" + normalized_path
            # Check for .. escape attempts
            if ".." in normalized_path.split("/"):
                write_failures.append({
                    "path": path,
                    "error": "Path traversal detected: path contains .."
                })
                print(f"[E2BSandbox] Path traversal attempt blocked: {path}")
                continue

            # Use the normalized path
            path = normalized_path
            print(f"[E2BSandbox] Writing file to: {path}")

            # Create parent directories (use shlex.quote to prevent command injection)
            # Since /workspace was already created with sudo and set to 777, subdirectories shouldn't need sudo
            # But as a safety measure, try sudo if regular mkdir fails
            dir_path = posixpath.dirname(path)
            if dir_path and dir_path not in created_dirs:
                try:
                    safe_dir_path = shlex.quote(dir_path)
                    mkdir_result = await _call_maybe_async(sandbox.commands.run, f"mkdir -p {safe_dir_path}")
                    exit_code = getattr(mkdir_result, "exit_code", None)
                    if exit_code is not None and exit_code != 0:
                        # Try using sudo
                        sudo_result = await _call_maybe_async(
                            sandbox.commands.run,
                            f"sudo mkdir -p {safe_dir_path} && sudo chmod 777 {safe_dir_path}"
                        )
                        sudo_code = getattr(sudo_result, "exit_code", None)
                        if sudo_code is not None and sudo_code != 0:
                            stderr = getattr(sudo_result, "stderr", "")
                            write_failures.append({"path": path, "error": f"Failed to create directory {dir_path}: exit_code={sudo_code}, stderr={stderr}"})
                            print(f"[E2BSandbox] Failed to create directory {dir_path} even with sudo: exit_code={sudo_code}")
                            continue
                        print(f"[E2BSandbox] Created directory with sudo: {dir_path}")
                    else:
                        print(f"[E2BSandbox] Created directory: {dir_path}")
                    created_dirs.add(dir_path)
                except Exception as e:
                    write_failures.append({"path": path, "error": f"Failed to create directory: {e}"})
                    print(f"[E2BSandbox] Exception creating directory {dir_path}: {e}")
                    continue

            # Write file content
            try:
                if isinstance(content, bytes):
                    await _call_maybe_async(sandbox.files.write, path, content)
                    print(f"[E2BSandbox] Wrote {len(content)} bytes to {path}")
                elif content is not None:
                    content_str = str(content)
                    await _call_maybe_async(sandbox.files.write, path, content_str)
                    print(f"[E2BSandbox] Wrote {len(content_str)} chars to {path}")
                else:
                    print(f"[E2BSandbox] Skipping {path}: content is None")
            except Exception as e:
                write_failures.append({"path": path, "error": str(e)})
                print(f"[E2BSandbox] Failed to write file {path}: {e}")

        # Merge all failed files
        all_failures = failed_files + write_failures

        result: dict[str, Any] = {"success": True}
        if all_failures:
            result["warnings"] = all_failures
        return result

    async def exec(self, session_id: str, command: str) -> dict:
        """Execute a command in the sandbox and return its output"""
        session, sandbox = await self._resolve(session_id)
        if not session:
            return {
                "success": False,
                "error": "Sandbox session not found. Call start first.",
            }

        # Execute in sandbox and normalize output to text.
        # Always run commands in /workspace so agent file operations land in the right place.
        try:
            result = await _call_maybe_async(sandbox.commands.run, command, cwd="/workspace")
            # E2B SDK v1+ uses .stdout/.stderr; older versions use .text
            output = getattr(result, "text", None)
            if output is None:
                stdout = getattr(result, "stdout", "")
                stderr = getattr(result, "stderr", "")
                output = stdout if stdout else stderr if stderr else str(result)

            # Check for error output (E2B may return errors in stderr)
            stderr = getattr(result, "stderr", None)
            exit_code = getattr(result, "exit_code", None)

            if exit_code is not None and exit_code != 0:
                # Command execution failed
                error_output = stderr if stderr else output
                return {
                    "success": False,
                    "error": f"Command failed with exit code {exit_code}: {error_output}",
                    "output": output,
                    "exit_code": exit_code
                }

            return {"success": True, "output": output}
        except Exception as e:
            error_msg = str(e)
            print(f"[E2BSandbox] Command execution failed: {error_msg}")
            return {
                "success": False,
                "error": f"Command execution failed: {error_msg}"
            }

    async def read(self, session_id: str) -> dict:
        """Read and parse JSON data from /workspace/data.json"""
        session, sandbox = await self._resolve(session_id)
        if not session:
            return {"success": False, "error": "Sandbox session not found"}

        raw = await _call_maybe_async(sandbox.files.read, "/workspace/data.json")
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            return {"success": True, "data": data}
        except Exception:
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
        session, sandbox = await self._resolve(session_id)
        if not session:
            return {"success": False, "error": "Sandbox session not found"}

        try:
            raw = await _call_maybe_async(sandbox.files.read, path)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            if parse_json:
                try:
                    data = json.loads(raw)
                    return {"success": True, "content": data}
                except json.JSONDecodeError:
                    return {"success": False, "error": f"Failed to parse JSON from {path}"}
            else:
                return {"success": True, "content": raw}
        except Exception as e:
            return {"success": False, "error": f"Failed to read {path}: {e!s}"}

    async def stop(self, session_id: str) -> dict:
        """Close and remove sandbox session"""
        session = self._store.get(session_id)
        if not session:
            return {"success": True}
        if not session.resource_id:
            return {"success": False, "error": "Sandbox session is still provisioning"}
        try:
            sandbox = await _call_maybe_async(
                self._sandbox_connector, session.resource_id
            )
            await _close_e2b(sandbox)
        except Exception as e:
            print(f"[E2BSandbox] Error closing sandbox {session_id}: {e}")
            return {"success": False, "error": "Failed to stop E2B sandbox; cleanup will retry"}
        self._store.delete(session_id)
        return {"success": True}

    async def status(self, session_id: str) -> dict:
        """Return session status and basic metadata"""
        session, _sandbox = await self._resolve(session_id)
        if not session:
            return {"active": False}
        return {
            "active": True,
            "sandbox_id": session.resource_id,
            "readonly": session.readonly,
            "created_at": session.created_at,
            "last_activity": session.last_activity,
        }

    async def stop_all(self) -> None:
        """Stop all sandbox sessions (used during service shutdown)"""
        for session in self._store.list_provider("e2b"):
            await self.stop(session.session_id)


def _default_e2b_factory():
    """Default factory: create an E2B sandbox instance"""
    from e2b_code_interpreter import Sandbox

    return Sandbox.create()


def _default_e2b_connector(sandbox_id: str):
    from e2b_code_interpreter import Sandbox

    return Sandbox.connect(sandbox_id)


async def _close_e2b(sandbox) -> None:
    close = getattr(sandbox, "kill", None) or getattr(sandbox, "close", None)
    if callable(close):
        await _call_maybe_async(close)


async def _call_maybe_async(func: Callable[..., Any], *args, **kwargs):
    """Run synchronous calls in a thread; directly await async calls"""
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    result = await asyncio.to_thread(func, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result
