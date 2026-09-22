"""Shared helpers for content router sub-modules."""

from __future__ import annotations

from src.version_engine.entrypoints.http.schemas import VersionEntryResponse
from src.version_engine.read.tree_reader import VersionEntry
from src.platform.auth.models import CurrentUser
from src.platform.authorization.models import ProjectAction
from src.platform.authorization.service import AuthorizationService


def ensure_project_access(
    authorization: AuthorizationService,
    current_user: CurrentUser,
    project_id: str,
):
    """Authorize a human content read through the canonical Project PDP."""
    return authorization.authorize(
        project_id, current_user.user_id, ProjectAction.CONTENT_READ
    )


def ensure_write_access(
    authorization: AuthorizationService,
    current_user: CurrentUser,
    project_id: str,
):
    """Authorize a human content write through the canonical Project PDP."""
    return authorization.authorize(
        project_id, current_user.user_id, ProjectAction.CONTENT_WRITE
    )


def entry_to_response(entry: VersionEntry) -> VersionEntryResponse:
    return VersionEntryResponse(
        name=entry.name,
        path=entry.path,
        type=entry.type,
        content_hash=entry.content_hash,
        size_bytes=entry.size_bytes,
        mime_type=entry.mime_type,
        children_count=entry.children_count,
        integrity_status=entry.integrity_status,
    )
