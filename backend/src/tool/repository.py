from __future__ import annotations

from abc import ABC, abstractmethod

from src.tool.supabase_repo import ToolRepository
from src.tool.models import Tool
from src.tool.supabase_schemas import (
    ToolCreate as SbToolCreate,
)
from src.tool.supabase_schemas import (
    ToolUpdate as SbToolUpdate,
)


class ToolRepositoryBase(ABC):
    @abstractmethod
    def create(self, tool: SbToolCreate) -> Tool:
        pass

    @abstractmethod
    def get_by_id(self, tool_id: str) -> Tool | None:
        pass

    @abstractmethod
    def get_by_org_id(
        self,
        org_id: str,
        *,
        skip: int = 0,
        limit: int = 100,
        path: str | None = None,
        project_id: str | None = None,
    ) -> list[Tool]:
        pass

    @abstractmethod
    def update(self, tool_id: str, tool: SbToolUpdate) -> Tool | None:
        pass

    @abstractmethod
    def delete(self, tool_id: str) -> bool:
        pass


class ToolRepositorySupabase(ToolRepositoryBase):
    def __init__(self, repository: ToolRepository):
        self._repo = repository

    def _to_model(self, resp) -> Tool:
        return Tool(
            id=resp.id,
            created_at=resp.created_at,
            created_by=resp.created_by if resp.created_by else None,
            org_id=resp.org_id or "",
            project_id=resp.project_id,
            path=resp.path,
            json_path=resp.json_path or "",
            type=resp.type or "",
            name=resp.name or "",
            alias=resp.alias,
            description=resp.description,
            input_schema=resp.input_schema,
            output_schema=resp.output_schema,
            metadata=resp.metadata,
            category=resp.category or "builtin",
            script_type=resp.script_type,
            script_content=resp.script_content,
        )

    def create(self, tool: SbToolCreate) -> Tool:
        resp = self._repo.create(tool)
        return self._to_model(resp)

    def get_by_id(self, tool_id: str) -> Tool | None:
        resp = self._repo.get_by_id(tool_id)
        if not resp:
            return None
        return self._to_model(resp)

    def get_by_org_id(
        self,
        org_id: str,
        *,
        skip: int = 0,
        limit: int = 100,
        path: str | None = None,
        project_id: str | None = None,
    ) -> list[Tool]:
        resps = self._repo.get_list(
            skip=skip, limit=limit, org_id=org_id, path=path, project_id=project_id
        )
        return [self._to_model(r) for r in resps]

    def update(self, tool_id: str, tool: SbToolUpdate) -> Tool | None:
        resp = self._repo.update(tool_id, tool)
        if not resp:
            return None
        return self._to_model(resp)

    def delete(self, tool_id: str) -> bool:
        return self._repo.delete(tool_id)
