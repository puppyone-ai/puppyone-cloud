from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Organization(BaseModel):
    id: str
    name: str
    slug: str
    avatar_url: str | None = None
    type: str = "team"
    # Only this explicit binding denotes personal ownership; legacy type does not.
    personal_owner_id: str | None = None
    plan: str = "free"
    seat_limit: int = 5
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrgMember(BaseModel):
    id: str
    org_id: str
    user_id: str
    role: str = "member"
    joined_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrgInvitation(BaseModel):
    id: str
    org_id: str
    email: str
    role: str = "member"
    token: str
    status: str = "pending"
    invited_by: str
    expires_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
