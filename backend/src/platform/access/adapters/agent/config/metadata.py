"""Resolve visible Agent paths under the entrypoint's existing Project grant."""

from collections.abc import Iterable

from src.file_operations.operation_adapter import ProductOperationAdapter
from src.platform.authorization.models import ProjectGrant


def read_path_metadata(
    ops: ProductOperationAdapter, grant: ProjectGrant, paths: Iterable[str]
) -> dict[str, dict[str, str]]:
    unique = set(paths)
    if not unique:
        return {}
    result = {}
    with ops.for_grant(grant).read_session(grant.project_id) as read:
        for path in sorted(unique):
            entry = read.stat(grant.project_id, path, include_children_count=False)
            if entry is not None:
                result[path] = {"name": entry.name, "type": entry.type}
    return result
