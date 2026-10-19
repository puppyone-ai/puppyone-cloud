"""Canonical, non-secret Git remote locators."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
_ROOT_RE = re.compile(r"^/git/([^/]+)\.git$")
_SCOPED_RE = re.compile(r"^/git/([^/]+)/scopes/([^/]+)\.git$")


@dataclass(frozen=True, slots=True)
class GitRemoteLocator:
    project_id: str
    scope_id: str | None = None

    @property
    def is_scoped(self) -> bool:
        return self.scope_id is not None


def validate_git_locator_id(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not _ID_RE.fullmatch(normalized):
        raise ValueError(f"Invalid canonical Git {field}")
    return normalized


def canonical_git_path(project_id: str, scope_id: str | None = None) -> str:
    project = validate_git_locator_id(project_id, field="project_id")
    if scope_id is None:
        return f"/git/{project}.git"
    scope = validate_git_locator_id(scope_id, field="scope_id")
    return f"/git/{project}/scopes/{scope}.git"


def canonical_git_url(
    cloud_origin: str,
    project_id: str,
    scope_id: str | None = None,
) -> str:
    parts = urlsplit(cloud_origin.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
    ):
        raise ValueError("cloud_origin must be a credential-free HTTP(S) origin")
    origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
    return f"{origin}{canonical_git_path(project_id, scope_id)}"


def parse_canonical_git_url(remote_url: str) -> GitRemoteLocator | None:
    parts = urlsplit(remote_url.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or "%" in parts.path
    ):
        return None
    path = parts.path
    scoped = _SCOPED_RE.fullmatch(path)
    root = _ROOT_RE.fullmatch(path)
    match = scoped or root
    if match is None:
        return None
    try:
        project_id = validate_git_locator_id(match.group(1), field="project_id")
        scope_id = (
            validate_git_locator_id(match.group(2), field="scope_id")
            if scoped
            else None
        )
    except ValueError:
        return None
    return GitRemoteLocator(project_id=project_id, scope_id=scope_id)
