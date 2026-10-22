from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class SandboxActionRequest(BaseModel):
    action: Literal["start", "exec", "read", "stop", "status"] = Field(
        ..., description="Sandbox action type"
    )
    session_id: str = Field(..., min_length=1, description="Sandbox session ID")
    data: Optional[Any] = Field(
        None, description="Data to write for the start action"
    )
    readonly: Optional[bool] = Field(
        False, description="Whether the start action is read-only"
    )
    command: Optional[str] = Field(
        None, description="Command to execute for the exec action"
    )
