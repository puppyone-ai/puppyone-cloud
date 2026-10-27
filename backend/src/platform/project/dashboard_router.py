"""Non-resource Dashboard sections; resource_dashboard alone owns HTTP routing.

Resource identities and usage are domain-qualified in resource_dashboard, never
joined through a mixed Connection DTO or unqualified ID set.
"""

from pydantic import BaseModel

from src.file_operations.operation_adapter import ProductOperationAdapter


class DashboardProject(BaseModel):
    id: str
    name: str
    description: str | None = None


class DashboardNodeCounts(BaseModel):
    total: int = 0
    folders: int = 0
    files: int = 0


class DashboardTool(BaseModel):
    id: str
    name: str
    type: str | None = None


class DashboardUpload(BaseModel):
    id: str
    status: str
    type: str
    progress: int = 0
    message: str | None = None


_NODE_COUNT_CACHE: dict[tuple[str, str], DashboardNodeCounts] = {}
_NODE_COUNT_CACHE_MAX = 1024


def _compute_node_counts(ops: ProductOperationAdapter, project_id: str) -> DashboardNodeCounts:
    """Cache complete Git tree counts by Project and head, never by path/name."""
    try:
        head_commit = ops.get_head_commit_id(project_id)
    except Exception:
        # A failed head read may use a live tree read, not an empty success.
        head_commit = ""

    cache_key = (project_id, head_commit)
    if head_commit and cache_key in _NODE_COUNT_CACHE:
        return _NODE_COUNT_CACHE[cache_key]

    all_entries = ops.list_tree(project_id, "", max_depth=-1)
    folder_count = sum(1 for entry in all_entries if entry.type == "folder")
    file_count = sum(1 for entry in all_entries if entry.type != "folder")
    counts = DashboardNodeCounts(
        total=folder_count + file_count, folders=folder_count, files=file_count,
    )
    if head_commit:
        if len(_NODE_COUNT_CACHE) >= _NODE_COUNT_CACHE_MAX:
            _NODE_COUNT_CACHE.clear()
        _NODE_COUNT_CACHE[cache_key] = counts
    return counts


def _fetch_tools(sb, project_id: str) -> list[DashboardTool]:
    tool_rows = (
        sb.table("tools")
        .select("id, name, type")
        .eq("project_id", project_id)
        .execute()
    ).data
    return [
        DashboardTool(id=tool["id"], name=tool["name"], type=tool.get("type"))
        for tool in tool_rows
    ]


def _fetch_uploads(sb, project_id: str) -> list[DashboardUpload]:
    # Dependency/schema failures must fail the inventory, never look like no jobs.
    upload_rows = (
        sb.table("uploads")
        .select("id, status, type, progress, message")
        .neq("type", "search_index")  # Legacy rows of the retired Search Tools.
        .eq("project_id", project_id)
        .in_("status", ["pending", "running"])
        .limit(20)
        .execute()
    ).data
    rows = upload_rows or []
    return [
        DashboardUpload(
            id=row["id"],
            status=row["status"],
            type=row.get("type", "file"),
            progress=row.get("progress", 0),
            message=row.get("message"),
        )
        for row in rows[:20]
    ]
