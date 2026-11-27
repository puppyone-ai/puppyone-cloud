"""Platform callbacks: verify, keep only what is addressed to the agent, store, 200.

No Agent work happens here. A redelivered event hits the inbox's unique key
and is acknowledged again. Also the signed-in "Link account" call.
"""

from __future__ import annotations

import html
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.auth.dependencies import get_current_user
from src.platform.chat_apps import credentials
from src.platform.chat_apps.dependencies import (
    get_chat_app_repository,
    get_chat_app_service,
    get_platforms,
)
from src.platform.chat_apps.ports import Inbound, NotVerified
from src.platform.chat_apps.schemas import LinkIdentity

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat-apps", tags=["chat-apps"])
WORKER = "api"


def _platform(name: str):
    platform = get_platforms().get(name)
    if platform is None:
        raise HTTPException(404)
    return platform


def store(repository, events: list[Inbound]) -> int:
    """Write addressed events to the inbox; returns how many were new."""
    stored = 0
    for event in events:
        connection = None
        if event.account_id is not None:
            row = repository.gateway(
                "connection", WORKER, platform=event.platform, external_id=event.account_id
            )
            if not row or row["status"] == "revoked":
                continue
            connection = row["id"]
        result = repository.gateway(
            "receive",
            WORKER,
            platform=event.platform,
            connection_id=connection,
            message_key=event.message_key,
            external_event_id=event.external_event_id,
            via=event.via,
            kind=event.kind,
            external_chat_id=event.external_chat_id,
            external_thread_key=event.external_thread_key,
            asker_external_id=event.asker_external_id,
            asker_label=event.asker_label,
            payload=event.payload(),
        )
        stored += 0 if result["duplicate"] else 1
    return stored


async def _intake(name: str, request: Request, repository):
    platform = _platform(name)
    raw = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    try:
        platform.verify(headers, raw)
    except NotVerified:
        raise HTTPException(401) from None
    answer = platform.handshake(headers, raw)
    if answer is not None:
        return JSONResponse(answer)
    events = platform.parse(headers, raw)
    if events:
        await run_blocking(store, repository, events)
    return JSONResponse({"ok": True})


@router.post("/telegram/webhook", include_in_schema=True)
async def telegram(request: Request, repository=Depends(get_chat_app_repository)):
    return await _intake("telegram", request, repository)


@router.post("/slack/events")
async def slack_events(request: Request, repository=Depends(get_chat_app_repository)):
    return await _intake("slack", request, repository)


@router.post("/slack/actions")
async def slack_actions(request: Request, repository=Depends(get_chat_app_repository)):
    return await _intake("slack", request, repository)


_INSTALL_COPY = {
    "en": {
        "added": "Added to {team}",
        "added_line": "PuppyOne is in your Slack. Go back to PuppyOne to invite it to a channel.",
        "failed": "Slack wasn't added",
        "failed_line": "Go back to PuppyOne and try again.",
        "close": "You can close this tab.",
    },
    "zh": {
        "added": "已加到 {team}",
        "added_line": "PuppyOne 已经在你的 Slack 里了。回到 PuppyOne，把它请进一个频道。",
        "failed": "没有加到 Slack",
        "failed_line": "回到 PuppyOne 再试一次。",
        "close": "你可以关闭这个标签页。",
    },
}


def _copy(request: Request) -> dict[str, str]:
    accept = (request.headers.get("accept-language") or "").lower()
    return _INSTALL_COPY["zh" if accept.startswith("zh") else "en"]


def _page(title: str, line: str, note: str, *, ok: bool, status: int = 200) -> HTMLResponse:
    """The browser tab Slack's install returns to, in the look of the sign-in
    result page: the product mark, one result, one line on what to do next."""
    logo = html.escape(f"{settings.FRONTEND_URL.rstrip('/')}/puppyone-logo.svg")
    tone = "#1f7a4d;background:#e3f2e9" if ok else "#a4331f;background:#fbe6e1"
    body = (
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title></head>"
        "<body style='margin:0;min-height:100vh;display:flex;align-items:center;"
        "justify-content:center;background:#ebe8e3;color:#1f2328;"
        'font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",system-ui,sans-serif\'>'
        "<main style='display:flex;flex-direction:column;align-items:center;gap:16px;"
        "max-width:340px;padding:24px;text-align:center'>"
        f"<img src='{logo}' alt='PuppyOne' width=48 height=48>"
        f"<h1 style='margin:0;font-size:22px;font-weight:600'>{html.escape(title)}</h1>"
        f"<p style='margin:0;padding:8px 14px;border-radius:6px;color:{tone}'>"
        f"{html.escape(line)}</p>"
        f"<p style='margin:0;font-size:12px;color:#6b7280'>{html.escape(note)}</p>"
        "</main></body></html>"
    )
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/slack/oauth/callback", include_in_schema=False)
async def slack_installed(request: Request, repository=Depends(get_chat_app_repository)):
    platform = _platform("slack")
    code, state = request.query_params.get("code"), request.query_params.get("state")
    if not code or not state:
        return _failed(request)
    from src.platform.chat_apps.codes import code_hash

    try:
        installed = await platform.exchange(code)
        await run_blocking(
            repository.gateway,
            "install_slack",
            WORKER,
            code_hash=code_hash(state),
            external_id=installed["team_id"],
            display_name=installed["team_name"],
            secret=credentials.seal(installed["bot_token"]),
            installer_external_id=installed["installer_user_id"],
        )
    except Exception:
        logger.warning("chat_app_slack_install_failed")
        return _failed(request)
    copy = _copy(request)
    return _page(
        copy["added"].format(team=installed["team_name"]),
        copy["added_line"],
        copy["close"],
        ok=True,
    )


def _failed(request: Request) -> HTMLResponse:
    copy = _copy(request)
    return _page(copy["failed"], copy["failed_line"], copy["close"], ok=False, status=400)


@router.post("/identities/preview")
def preview_identity(
    body: LinkIdentity, user=Depends(get_current_user), service=Depends(get_chat_app_service)
):
    return service.preview_identity(user.user_id, body.code)


@router.post("/identities")
def link_identity(
    body: LinkIdentity, user=Depends(get_current_user), service=Depends(get_chat_app_service)
):
    return service.link_identity(user.user_id, body.code)
