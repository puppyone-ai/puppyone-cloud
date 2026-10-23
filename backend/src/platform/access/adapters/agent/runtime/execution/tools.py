"""Reuse configured search tools; no worker-selected Project or credential."""

import json
import re
from types import SimpleNamespace

from src.file_operations.queries import run_read
from src.infra.execution.runner import run_owned
from src.infra.search.schemas import SearchToolQueryInput
from src.platform.access.adapters.agent.runtime.control.workspace import allowed


class BoundTools:
    def __init__(self, admission, search):
        self.admission = admission
        self.search = search

    def definitions(self, run):
        tools = []
        for definition in run["policy"]["tool_definitions"]:
            tool = SimpleNamespace(**definition)
            if tool is None or tool.project_id != run["project_id"]:
                raise PermissionError("Agent tool belongs to another Project")
            if tool.type != "search" or (
                tool.path is None or not allowed(tool.path, run["policy"])
            ):
                raise PermissionError("Agent tool exceeds the execution policy")
            tools.append(
                {
                    "name": "search_" + re.sub("[^a-zA-Z0-9_]", "_", tool.id),
                    "description": tool.description or tool.name,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                }
            )
        return tools

    async def execute(self, run, name, values):
        candidate = next(
            (
                tid
                for tid in run["policy"]["tool_ids"]
                if name == "search_" + re.sub("[^a-zA-Z0-9_]", "_", tid)
            ),
            None,
        )
        if candidate is None:
            raise PermissionError("Tool is not bound to this Agent")
        definition = next(
            (value for value in run["policy"]["tool_definitions"] if value["id"] == candidate), None
        )
        tool = SimpleNamespace(**definition) if definition else None
        if (
            tool is None
            or tool.type != "search"
            or tool.project_id != run["project_id"]
            or (tool.path is None or not allowed(tool.path, run["policy"]))
        ):
            raise PermissionError("Tool scope changed")
        value = SearchToolQueryInput.model_validate(values)
        # Scope service performs the canonical indexed lookup; Project identity
        # and search boundary always come from the persisted tool configuration.
        ops = await run_owned(self.search._ops.for_user, run["project_id"], run["user_id"])
        entry = await run_read(ops, run["project_id"], lambda read: read.stat(run["project_id"], tool.path))
        if entry is None:
            raise FileNotFoundError(tool.path)
        if entry.type == "folder":
            result = await self.search.search_folder(
                project_id=run["project_id"],
                folder_path=tool.path,
                query=value.query,
                top_k=value.top_k,
            )
            result = [
                item
                for item in result
                if item.get("file", {}).get("version_path")
                and allowed(item["file"]["version_path"], run["policy"])
            ]
        else:
            result = await self.search.search_scope(
                project_id=run["project_id"],
                path=tool.path,
                tool_json_path=tool.json_path,
                query=value.query,
                top_k=value.top_k,
            )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, default=str)[:100000],
                }
            ]
        }
