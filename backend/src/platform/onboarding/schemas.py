"""First sign-in response."""

from pydantic import BaseModel


class InitializeResponse(BaseModel):
    org_id: str
    is_new_org: bool
    demo_project_id: str | None = None
