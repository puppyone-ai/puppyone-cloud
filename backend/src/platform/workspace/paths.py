"""Safe path construction for Project-owned host workspace state."""

from __future__ import annotations

import os
import re
from pathlib import Path

SAFE_STORAGE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def validate_storage_segment(value: str, *, label: str) -> str:
    """Return a single safe storage segment or fail before touching disk."""

    if not isinstance(value, str) or not SAFE_STORAGE_SEGMENT.fullmatch(value):
        raise ValueError(f"{label} must be a single safe storage path segment")
    return value


def absolute_path(path: str | os.PathLike[str]) -> Path:
    """Normalize a trusted configured root without resolving child symlinks."""

    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def project_child(root: str | os.PathLike[str], project_id: str) -> Path:
    """Construct exactly ``root/<project_id>`` from a validated segment."""

    return absolute_path(root) / validate_storage_segment(
        project_id,
        label="project_id",
    )


def agent_child(root: str | os.PathLike[str], agent_id: str) -> Path:
    """Construct exactly ``root/<agent_id>`` from a validated segment."""

    return absolute_path(root) / validate_storage_segment(agent_id, label="agent_id")


def content_child(root: str | os.PathLike[str], relative_path: str) -> Path:
    """Build a contained Project content path while preserving nested names."""

    if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
        raise ValueError("content path must be a non-empty relative path")
    relative = Path(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("content path escapes the Project cache")

    normalized_root = absolute_path(root)
    if normalized_root.is_symlink():
        raise ValueError("content cache root must not be a symlink")
    candidate = normalized_root.joinpath(*relative.parts)
    try:
        resolved_root = normalized_root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        contained = os.path.commonpath((str(resolved_root), str(resolved_candidate)))
    except ValueError as exc:
        raise ValueError("content path escapes the Project cache") from exc
    if contained != str(resolved_root):
        raise ValueError("content path escapes the Project cache")
    return candidate
