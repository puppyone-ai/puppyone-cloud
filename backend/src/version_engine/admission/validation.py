"""Input validation utilities for the version engine.

Provides path sanitization, size limits, and depth checks to prevent
path traversal, resource exhaustion, and other input-based attacks.
"""

from __future__ import annotations

import re

from fastapi import HTTPException

# ── Limits ──

# Mandatory infrastructure ceiling. Plan-specific limits are enforced at the
# authoritative publish boundary from the PuppyPay entitlement snapshot.
MAX_FILE_SIZE = 500 * 1024 * 1024
MAX_FILES_PER_PUSH = 1000
MAX_PATH_LENGTH = 500
MAX_PUSH_BODY_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB total push payload


# ── Helpers ──


def _format_size(num_bytes: int) -> str:
    """Render a byte count as a short human label (e.g. ``53.4 MB``)."""
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


_SINGLE_FILE_HINT = (
    "Split the file into smaller parts before committing, or remove it from "
    "this push and use the file-upload API. The active organization plan may "
    "apply a lower per-file limit at publish time."
)
_TOTAL_BODY_HINT = (
    "Total push payload exceeds 2 GB. Split into smaller commits "
    "(e.g. `git commit <subset>` then `git push`, repeat) or push "
    "binary blobs separately via the file-upload API."
)
_TOO_MANY_FILES_HINT = (
    "Too many files in one push. Stage and push in batches of <=1000 files at a time."
)

# ── Path Validation ──

_FORBIDDEN_SEGMENTS = frozenset({"..", ".", "~"})
_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_FORBIDDEN_FILENAMES = frozenset(
    [".", "..", "CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def validate_path(path: str) -> str:
    """Sanitize and validate a content path.

    Strips leading/trailing slashes, rejects traversal attempts
    and excessively long paths.

    Returns the cleaned path.

    Raises:
        HTTPException 400 on invalid path.
    """
    clean = path.strip("/")

    if len(clean) > MAX_PATH_LENGTH:
        raise HTTPException(400, f"Path exceeds {MAX_PATH_LENGTH} characters")

    if clean:
        segments = clean.split("/")
        for seg in segments:
            if seg in _FORBIDDEN_SEGMENTS:
                raise HTTPException(400, f"Invalid path segment: '{seg}'")
            if "\x00" in seg or "\\" in seg:
                raise HTTPException(400, "Path contains invalid characters")

    return clean


def validate_version_filename(filename: str) -> str | None:
    """Return an error message when a version-tree path is invalid."""
    if not filename or not filename.strip():
        return "Filename must not be empty"
    if filename.startswith("/"):
        return f"Absolute path not allowed: {filename}"
    if "\\" in filename:
        return f"Backslash not allowed: {filename}"

    segments = filename.split("/")
    if any(segment == "" for segment in segments):
        return f"Double slash not allowed: {filename}"
    if any(segment == "." for segment in segments):
        return f"Relative path not allowed: {filename}"
    if any(segment == ".." for segment in segments):
        return f"Path traversal not allowed: {filename}"
    if _FORBIDDEN_FILENAME_CHARS.search(filename):
        return f"Filename contains forbidden characters: {filename}"
    basename = filename.rsplit("/", 1)[-1].split(".")[0].upper()
    if basename in _FORBIDDEN_FILENAMES:
        return f"Reserved filename: {filename}"
    if len(filename) > 255:
        return "Filename too long (max 255)"
    return None


def validate_limit(limit: int, default: int = 100, maximum: int = 1000) -> int:
    """Clamp limit to safe range."""
    if limit <= 0:
        return default
    return min(limit, maximum)


def validate_content_size(content: bytes, max_size: int = MAX_FILE_SIZE) -> None:
    """Reject content that exceeds the file size limit.

    Raises HTTPException 413 with an actionable hint if content is too large.
    """
    if len(content) > max_size:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File size {_format_size(len(content))} exceeds the "
                f"{_format_size(max_size)} limit. {_SINGLE_FILE_HINT}"
            ),
        )


def validate_push_objects(body: dict) -> None:
    """Validate object sizes in a version push request body.

    Checks each base64-encoded object blob against MAX_FILE_SIZE and
    the total payload against MAX_PUSH_BODY_SIZE.

    Raises HTTPException 413 with an actionable hint if any limit is exceeded.
    """
    objects = body.get("objects")
    if not isinstance(objects, dict):
        return

    if len(objects) > MAX_FILES_PER_PUSH:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Push contains {len(objects)} objects, exceeds the "
                f"{MAX_FILES_PER_PUSH}-object limit. {_TOO_MANY_FILES_HINT}"
            ),
        )

    total_size = 0
    for obj_hash, b64_data in objects.items():
        if not isinstance(b64_data, str):
            continue
        raw_size = len(b64_data) * 3 // 4
        if raw_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Object {obj_hash[:16]}... size ~{_format_size(raw_size)} "
                    f"exceeds the {_format_size(MAX_FILE_SIZE)} per-file limit. "
                    f"{_SINGLE_FILE_HINT}"
                ),
            )
        total_size += raw_size

    if total_size > MAX_PUSH_BODY_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Total push payload ~{_format_size(total_size)} exceeds the "
                f"{_format_size(MAX_PUSH_BODY_SIZE)} limit. {_TOTAL_BODY_HINT}"
            ),
        )
