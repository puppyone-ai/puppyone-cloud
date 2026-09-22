"""
L2.5 Sync — CacheManager Local Cache Management

Manages cache metadata for local Lower directories:
- Read/write .metadata.json (records sync timestamps for each node)
- Manage directory structure
- Clean up expired cache

Pure file system operations extracted from workspace/sync_worker.py.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any

from src.platform.workspace.paths import absolute_path, content_child, project_child
from src.utils.logger import log_debug, log_error


class CacheManager:
    """Local cache directory management."""

    def __init__(self, base_dir: str = "/tmp/contextbase"):
        self._base_dir = str(absolute_path(base_dir))
        self._lower_dir = str(absolute_path(base_dir) / "lower")
        if Path(self._lower_dir).is_symlink():
            raise ValueError("Lower cache root must not be a symlink")
        Path(self._lower_dir).mkdir(parents=True, exist_ok=True)

    @property
    def lower_dir(self) -> str:
        return self._lower_dir

    def get_project_path(self, project_id: str) -> str:
        """Return the exact Lower path without creating it."""

        return str(project_child(self._lower_dir, project_id))

    def get_project_dir(self, project_id: str) -> str:
        """Get the Lower directory path for a project (auto-creates)."""

        path = Path(self.get_project_path(project_id))
        if path.is_symlink():
            raise ValueError("Project Lower cache path must not be a symlink")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    # ============================================================
    # Metadata Management
    # ============================================================

    def read_metadata(self, project_id: str) -> dict[str, Any]:
        """Read sync metadata for a project."""
        meta_path = os.path.join(self.get_project_dir(project_id), ".metadata.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def write_metadata(self, project_id: str, metadata: dict[str, Any]) -> None:
        """Write sync metadata for a project."""
        meta_path = os.path.join(self.get_project_dir(project_id), ".metadata.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    # ============================================================
    # File Writing
    # ============================================================

    def write_file(self, project_id: str, filename: str, content: str) -> bool:
        """Write a text file to the Lower directory."""
        try:
            project_path = self.get_project_path(project_id)
            file_path = content_child(project_path, filename)
            Path(project_path).mkdir(parents=True, exist_ok=True)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with file_path.open("w", encoding="utf-8") as f:
                f.write(content)
            return True
        except (OSError, ValueError) as e:
            log_error(f"[CacheManager] Failed to write {filename}: {e}")
            return False

    def write_bytes(self, project_id: str, filename: str, data: bytes) -> bool:
        """Write a binary file to the Lower directory."""
        try:
            project_path = self.get_project_path(project_id)
            file_path = content_child(project_path, filename)
            Path(project_path).mkdir(parents=True, exist_ok=True)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with file_path.open("wb") as f:
                f.write(data)
            return True
        except (OSError, ValueError) as e:
            log_error(f"[CacheManager] Failed to write bytes {filename}: {e}")
            return False

    # ============================================================
    # Cleanup
    # ============================================================

    def clean_project(self, project_id: str) -> None:
        """Clean up the cache directory for a project."""
        project_dir = Path(self.get_project_path(project_id))
        if project_dir.is_symlink():
            project_dir.unlink(missing_ok=True)
            log_debug(f"[CacheManager] Cleaned cache for project {project_id}")
        elif project_dir.exists():
            shutil.rmtree(project_dir)
            log_debug(f"[CacheManager] Cleaned cache for project {project_id}")

    def get_cache_size(self, project_id: str) -> int:
        """Get the total size of the project cache (bytes)."""
        total = 0
        project_dir = self.get_project_path(project_id)
        if not os.path.isdir(project_dir):
            return 0
        for root, _, files in os.walk(project_dir):
            for f in files:
                total += os.path.getsize(os.path.join(root, f))
        return total
