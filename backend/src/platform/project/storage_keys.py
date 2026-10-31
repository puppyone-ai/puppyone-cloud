"""Which object storage keys belong to a Project."""

from __future__ import annotations

import re

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_PROJECT_KEY_PATTERNS = (
    re.compile(rf"^(?:version|projects|shadow-snapshots)/(?P<project>{_SEGMENT})/"),
    re.compile(
        rf"^users/{_SEGMENT}/(?:etl_artifacts|processed|raw)/"
        rf"(?P<project>{_SEGMENT})/"
    ),
)


def project_id_from_owned_s3_key(key: str) -> str | None:
    for pattern in _PROJECT_KEY_PATTERNS:
        matched = pattern.match(key)
        if matched:
            return matched.group("project")
    return None
