"""Shared HTTP read context and error mapping; no route or write authority."""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager

from fastapi import HTTPException

from src.version_engine.admission.validation import MAX_PATH_LENGTH, validate_path
from src.version_engine.domain.errors import (
    ObjectNotFoundError,
    PathNotFoundError,
    RepositoryRefNotFoundError,
    RepositoryRefTypeError,
    VersionReadError,
)
from src.version_engine.entrypoints.http.content_helpers import display_git_path


def _read_request_path(path: str, encoded: str | None) -> str:
    if encoded is None:
        return validate_path(path)
    if path:
        raise HTTPException(400, "Provide path or path_bytes_b64, not both")
    # Preserve the text API's character ceiling (UTF-8 uses at most four
    # bytes per character) while bounding decoding before allocating raw bytes.
    if len(encoded) > ((MAX_PATH_LENGTH * 4 + 2) // 3) * 4:
        raise HTTPException(400, "Invalid byte path")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, "Invalid byte path") from exc
    decoded = raw.decode("utf-8", "surrogateescape")
    if (
        len(decoded) > MAX_PATH_LENGTH
        or b"\x00" in raw
        or (raw and any(part in {b"", b".", b".."} for part in raw.split(b"/")))
    ):
        raise HTTPException(400, "Invalid byte path")
    return decoded


@contextmanager
def _product_read(ops, project_id, grant, *, byte_path=False, ref_b64=None):
    from src.version_engine.read.ref_metadata import decode_ref_name

    selection = {}
    if ref_b64 is not None:
        try:
            selection["selector"] = decode_ref_name(ref_b64)
        except ValueError as exc:
            raise HTTPException(400, "Invalid repository ref selector") from exc
    try:
        with ops.open_read(project_id, grant, **selection) as read:
            revision = read.get_read_revision(project_id)
            if revision is None:
                if ref_b64 is not None:
                    raise HTTPException(400, "Ref selection requires native repository authority")
                if byte_path:
                    raise HTTPException(400, "Byte paths require native repository authority")
            yield read, revision
    except RepositoryRefNotFoundError as exc:
        raise HTTPException(404, "Repository ref not found") from exc
    except RepositoryRefTypeError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (
        HTTPException,
        ObjectNotFoundError,
        FileNotFoundError,
        PathNotFoundError,
        VersionReadError,
    ):
        raise
    except PermissionError as exc:
        raise HTTPException(403, "Repository action denied") from exc
    except Exception as exc:
        # A grant and a pin establish provenance, not ongoing authority. SQL
        # may deny an actor after the PDP check (including after lock waits).
        from postgrest.exceptions import APIError

        if isinstance(exc, APIError) and exc.message == "repository_action_denied":
            raise HTTPException(403, "Repository action denied") from exc
        raise HTTPException(503, "Repository read is temporarily unavailable") from exc


def _raise_storage_integrity_error(project_id: str, path: str, exc: ObjectNotFoundError) -> None:
    path = display_git_path(path)
    raise HTTPException(
        status_code=500,
        detail={
            "code": "VERSION_STORAGE_INTEGRITY_ERROR",
            "message": f"Canonical repository bytes are unavailable while reading {path or 'project root'}.",
            "path": path,
        },
    ) from exc


def _raise_directory_not_found(path: str, exc: PathNotFoundError) -> None:
    path = display_git_path(path)
    target = path or "project root"
    raise HTTPException(
        status_code=404,
        detail={
            "code": "DIRECTORY_NOT_FOUND",
            "message": f"Directory not found: {target}",
            "path": path,
        },
    ) from exc


def _raise_version_read_error(path: str, exc: VersionReadError) -> None:
    path = display_git_path(path)
    target = path or "project root"
    raise HTTPException(
        status_code=502,
        detail={
            "code": "VERSION_READ_UNAVAILABLE",
            "message": f"Version tree temporarily unavailable while reading {target}.",
            "path": path,
        },
    ) from exc
