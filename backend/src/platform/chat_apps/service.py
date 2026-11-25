"""What managers do in Desktop: add a chat, let it in, change it, take over, remove."""

from __future__ import annotations

import logging

from fastapi import HTTPException

from src.platform.chat_apps import codes, credentials
from src.platform.chat_apps.repository import ChatAppRepository
from src.platform.chat_apps.schemas import ChangeChat, ConnectBot, StartLink

logger = logging.getLogger(__name__)


class ChatAppService:
    def __init__(self, repository: ChatAppRepository, platforms: dict, agents=None):
        self.repository = repository
        self.platforms = platforms
        self.agents = agents

    def _platform(self, name: str):
        platform = self.platforms.get(name)
        if platform is None:
            raise HTTPException(404, detail={"code": "chat_app_platform_off"})
        return platform

    def list(self, user: str, project: str):
        value = self.repository.human("list", user, project_id=project)
        value["platforms"] = sorted(self.platforms)
        return value

    def start(self, user: str, project: str, body: StartLink):
        platform = self._platform(body.platform)
        connection = str(body.connection_id) if body.connection_id else None
        if body.platform == "telegram":
            raw = codes.link_code()
            link = self._start(user, project, "telegram", "knock", raw)
            return {"link": link, "links": platform.start_links(raw)}
        if body.platform == "wecom":
            if connection is None:
                raise HTTPException(422, detail={"code": "chat_app_invalid_request"})
            raw = codes.short_code()
            link = self._start(user, project, "wecom", "knock", raw, connection)
            return {"link": link, "code": raw}
        if connection is not None:
            link = self._start(user, project, "slack", "slack_wait", codes.link_code(), connection)
            return {"link": link, "invite": "/invite @PuppyOne"}
        raw = codes.link_code()
        link = self._start(user, project, "slack", "slack_install", raw)
        return {"link": link, "install_url": platform.authorize_url(raw)}

    def _start(self, user, project, platform, purpose, raw, connection=None):
        return self.repository.human(
            "start_link",
            user,
            project_id=project,
            platform=platform,
            purpose=purpose,
            code_hash=codes.code_hash(raw),
            connection_id=connection,
        )

    def link_status(self, user: str, project: str, link_id: str):
        return self.repository.human("link_status", user, project_id=project, link_id=link_id)

    def connect(self, user: str, project: str, body: ConnectBot):
        self._platform("wecom")
        return self.repository.human(
            "connect",
            user,
            project_id=project,
            platform="wecom",
            external_id=body.bot_id.strip(),
            display_name=(body.display_name or "").strip() or None,
            secret=credentials.seal(body.secret.strip()),
        )

    def decide(self, user: str, project: str, chat: str, decision: str):
        return self.repository.human(
            "decide", user, project_id=project, conversation_id=chat, decision=decision
        )

    def change(self, user: str, project: str, chat: str, body: ChangeChat):
        values = body.model_dump(exclude_unset=True)
        if "enabled" in values and values["enabled"] is None:
            raise HTTPException(422, detail={"code": "chat_app_invalid_request"})
        return self.repository.human(
            "update", user, project_id=project, conversation_id=chat, **values
        )

    def take_over(self, user: str, project: str, chat: str):
        return self.repository.human("take_over", user, project_id=project, conversation_id=chat)

    def remove(self, user: str, project: str, chat: str):
        value = self.repository.human("remove", user, project_id=project, conversation_id=chat)
        # Running Runs belong to the owner; only the submitter may stop them.
        for run in value.pop("stop_runs", []) or []:
            try:
                self.agents.command(value["owner_user_id"], run, "stop")
            except HTTPException:
                logger.info("chat_app_remove_stop_skipped", extra={"chat_app": {"run": run}})
        return value

    def link_identity(self, user: str, code: str):
        return self.repository.human("link_identity", user, code_hash=codes.code_hash(code))
