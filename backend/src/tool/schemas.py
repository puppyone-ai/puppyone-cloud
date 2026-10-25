from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ToolTypeKey = Literal[
    "get_data_schema",
    "get_all_data",
    "query_data",
    "create",
    "update",
    "delete",
    "preview",
    "select",
]
# Search Tools were retired with the vector search backend. Existing rows stay
# readable (and deletable) but can no longer be created or switched to.
StoredToolType = ToolTypeKey | Literal["search"]


ToolCategory = Literal["builtin", "custom"]


class ToolCreate(BaseModel):
    path: str | None = Field(
        default=None,
        description="version path (required for built-in tools, optional for custom multi-node tools)"
    )
    json_path: str = Field(
        default="",
        description="JSON Pointer path (mount point, RFC6901). Empty string means root path.",
        examples=["", "/articles", "/0/content"],
    )
    type: ToolTypeKey = Field(
        ...,
        description="Tool type (note: shell_access has been moved to the agent_bash table)",
        examples=["create", "query_data", "custom_script"],
    )

    name: str = Field(..., description="Unique tool invocation name (should be unique within the same MCP)")
    alias: str | None = Field(default=None, description="Display name for frontend (can be duplicated)")
    description: str | None = Field(default=None, description="Tool description")

    input_schema: Any | None = Field(
        default=None, description="JSON Schema (input), empty for non-custom Tools"
    )
    output_schema: Any | None = Field(
        default=None, description="JSON Schema (output), empty for non-custom Tools"
    )
    metadata: Any | None = Field(
        default=None,
        description=(
            "Extension config (by tool.type convention).\n\n"
            "- Binding info for custom multi-node tools is stored here: bound_nodes: [{path, role, alias}]\n"
        ),
        examples=[{"preview_keys": ["id", "title"]}, {"bound_nodes": [{"path": "xxx", "role": "source"}]}],
    )

    # Additional fields
    category: ToolCategory = Field(
        default="builtin",
        description="Tool category: builtin or custom (custom script)"
    )
    script_type: str | None = Field(
        default=None,
        description="Script type (only for custom category): python, javascript, shell"
    )
    script_content: str | None = Field(
        default=None,
        description="Script code content (only for custom category)"
    )


class ToolUpdate(BaseModel):
    path: str | None = None
    json_path: str | None = None
    type: ToolTypeKey | None = None

    name: str | None = None
    alias: str | None = None
    description: str | None = None

    input_schema: Any | None = None
    output_schema: Any | None = None
    metadata: Any | None = None

    # Additional fields
    category: ToolCategory | None = None
    script_type: str | None = None
    script_content: str | None = None


class ToolOut(BaseModel):
    id: str
    created_at: datetime

    created_by: str | None = None
    org_id: str
    project_id: str | None = None  # Associated project ID
    path: str | None = None
    json_path: str = ""

    type: StoredToolType
    name: str
    alias: str | None = None
    description: str | None = None

    input_schema: Any | None = None
    output_schema: Any | None = None
    metadata: Any | None = None

    # Additional fields
    category: ToolCategory = "builtin"
    script_type: str | None = None
    script_content: str | None = None
