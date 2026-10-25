from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ToolCategory = Literal["builtin", "custom"]


class Tool(BaseModel):
    id: str
    created_at: datetime

    created_by: str | None = None
    org_id: str
    project_id: str | None = None  # Associated project ID (for filtering by project)
    path: str | None = None  # version path
    json_path: str = ""  # JSON internal path (e.g. /users/0)

    type: str  # Tool type: query_data, create, update, delete, custom_script, etc. (note: shell_access moved to agent_bash)
    name: str
    alias: str | None = None
    description: str | None = None

    input_schema: Any | None = Field(default=None, description="JSON Schema (input)")
    output_schema: Any | None = Field(
        default=None, description="JSON Schema (output)"
    )
    metadata: Any | None = Field(default=None, description="Extension config (multi-node bindings for custom tools stored here)")

    # Additional fields
    category: ToolCategory = "builtin"  # Tool category: builtin or custom
    script_type: str | None = None  # Script type: python, javascript, shell (only for custom category)
    script_content: str | None = None  # Script code content (only for custom category)
