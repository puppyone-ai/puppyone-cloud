"""Request shapes of the Desktop routes. Responses are the command functions' JSON."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartLink(Strict):
    platform: Literal["telegram", "slack", "wecom"]
    #: Slack: an installed workspace to wait in; WeCom: the connected bot.
    connection_id: UUID | None = None


class ConnectBot(Strict):
    platform: Literal["wecom"] = "wecom"
    bot_id: str = Field(min_length=1, max_length=200)
    secret: str = Field(min_length=1, max_length=2000, repr=False)
    display_name: str | None = Field(default=None, max_length=200)


class Decision(Strict):
    decision: Literal["allow", "deny"]


class ChangeChat(Strict):
    enabled: bool | None = None
    who_can_ask: Literal["owner", "project", "anyone"] | None = None
    #: Minutes per UTC day; null = no limit. Absent = unchanged.
    daily_limit_units: int | None = Field(default=None, gt=0, le=24 * 60)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)


class LinkIdentity(Strict):
    code: str = Field(min_length=16, max_length=128, repr=False)
