"""Callbacks, configuration and the Desktop link start, with fakes only."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.config import Settings
from src.platform.chat_apps import codes, credentials
from src.platform.chat_apps import webhook_router as hooks
from src.platform.chat_apps.dependencies import get_chat_app_repository
from src.platform.chat_apps.schemas import ChangeChat, ConnectBot, StartLink
from src.platform.chat_apps.service import ChatAppService
from tests.platform.chat_apps.unit.support import FakePlatform

KEY = base64.b64encode(bytes(range(32))).decode()  # a fixed test key, made here


@pytest.fixture
def secrets(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "CHAT_APPS_CODE_HASH_SECRET", "code-hash-test-placeholder")
    monkeypatch.setattr(settings, "CHAT_APPS_ENCRYPTION_KEY", KEY)


def intake(monkeypatch, platforms):
    repository = Mock()
    repository.gateway.side_effect = lambda action, worker, **payload: (
        {"id": "conn-1", "status": "active"} if action == "connection" else {"duplicate": False}
    )
    monkeypatch.setattr(hooks, "get_platforms", lambda: platforms)
    app = FastAPI()
    app.include_router(hooks.router, prefix="/api/v1")
    app.dependency_overrides[get_chat_app_repository] = lambda: repository
    return TestClient(app), repository


def test_platforms_that_are_off_answer_404(monkeypatch):
    client, repository = intake(monkeypatch, {})
    assert client.post("/api/v1/chat-apps/telegram/webhook", content=b"{}").status_code == 404
    assert client.post("/api/v1/chat-apps/slack/events", content=b"{}").status_code == 404
    repository.gateway.assert_not_called()


def test_an_unsigned_callback_is_refused_and_not_stored(monkeypatch):
    client, repository = intake(monkeypatch, {"telegram": FakePlatform()})
    response = client.post("/api/v1/chat-apps/telegram/webhook",
                           content=json.dumps({"events": [{"message_key": "1", "via": "message",
                                                           "kind": "question", "external_chat_id": "c"}]}))
    assert response.status_code == 401
    repository.gateway.assert_not_called()


def test_addressed_events_are_stored_then_acknowledged(monkeypatch):
    client, repository = intake(monkeypatch, {"slack": FakePlatform(name="slack")})
    body = {"events": [{"message_key": "T1:C1:1.0", "via": "message", "kind": "question",
                        "external_chat_id": "C1", "account_id": "T1", "text": "hi"}]}
    response = client.post("/api/v1/chat-apps/slack/events", content=json.dumps(body),
                           headers={"x-fake-secret": "fake-webhook-placeholder"})
    assert response.status_code == 200
    actions = [call.args[0] for call in repository.gateway.call_args_list]
    assert actions == ["connection", "receive"]
    stored = repository.gateway.call_args_list[1].kwargs
    assert stored["connection_id"] == "conn-1" and stored["payload"]["text"] == "hi"


def test_handshakes_answer_without_storing(monkeypatch):
    client, repository = intake(monkeypatch, {"slack": FakePlatform(name="slack")})
    response = client.post("/api/v1/chat-apps/slack/events", content=b'{"challenge":"c1"}',
                           headers={"x-fake-secret": "fake-webhook-placeholder"})
    assert response.json() == {"challenge": "c1"}
    repository.gateway.assert_not_called()


def test_events_from_a_revoked_workspace_are_dropped():
    repository = Mock()
    repository.gateway.return_value = {"id": "x", "status": "revoked"}
    from src.platform.chat_apps.ports import Inbound

    event = Inbound(platform="slack", message_key="k", via="message", kind="question",
                    external_chat_id="C", account_id="T")
    assert hooks.store(repository, [event]) == 0


BASE = dict(APP_ENV="test", SERVICE_ROLE="api", FRONTEND_URL="https://web.test",
            PUBLIC_URL="https://api.test")


def test_enabled_platforms_without_secrets_fail_closed():
    with pytest.raises(ValueError, match="CHAT_APPS_ENCRYPTION_KEY"):
        Settings(**BASE, CHAT_APPS_PLATFORMS="telegram")
    with pytest.raises(ValueError, match="unknown platforms"):
        Settings(**BASE, CHAT_APPS_PLATFORMS="whatsapp")
    full = dict(BASE, CHAT_APPS_PLATFORMS="telegram,slack,wecom", CHAT_APPS_ENCRYPTION_KEY=KEY,
                CHAT_APPS_CODE_HASH_SECRET="x" * 40, CHAT_APPS_TELEGRAM_BOT_TOKEN="1:placeholder",
                CHAT_APPS_TELEGRAM_BOT_USERNAME="PuppyOneBot",
                CHAT_APPS_TELEGRAM_WEBHOOK_SECRET="hook_placeholder",
                CHAT_APPS_SLACK_CLIENT_ID="id", CHAT_APPS_SLACK_CLIENT_SECRET="placeholder",
                CHAT_APPS_SLACK_SIGNING_SECRET="placeholder")
    assert Settings(**full).chat_app_platforms == {"telegram", "slack", "wecom"}
    with pytest.raises(ValueError, match="SLACK_SIGNING_SECRET"):
        Settings(**{**full, "CHAT_APPS_SLACK_SIGNING_SECRET": ""})


def test_only_api_and_chat_gateway_check_chat_secrets_and_off_means_off():
    assert Settings(**{**BASE, "SERVICE_ROLE": "agent_worker"}, CHAT_APPS_PLATFORMS="telegram")
    assert Settings(**BASE).chat_app_platforms == frozenset()


def test_secrets_round_trip_and_codes_are_hashed(secrets):
    sealed = credentials.seal("xoxb-test-placeholder")
    assert "xoxb" not in sealed and credentials.unseal(sealed) == "xoxb-test-placeholder"
    assert codes.code_hash("abc") == codes.code_hash(" abc ") != codes.code_hash("abd")
    assert len(codes.short_code()) == 6 and codes.short_code().isdigit()
    assert 16 <= len(codes.link_code()) <= 64


def service(platforms=None):
    repository = Mock()
    repository.human.side_effect = lambda action, user, **payload: {"action": action, **payload}
    telegram = SimpleNamespace(start_links=lambda code: {"group": f"g?{code}", "dm": f"d?{code}"})
    slack = SimpleNamespace(authorize_url=lambda state: f"https://slack.test/oauth?state={state}")
    return ChatAppService(repository, platforms or {"telegram": telegram, "slack": slack,
                                                    "wecom": object()}, agents=Mock()), repository


def test_each_app_starts_its_own_way(secrets):
    svc, repository = service()
    telegram = svc.start("u", "p", StartLink(platform="telegram"))
    code = telegram["links"]["group"].split("?", 1)[1]
    assert telegram["link"]["code_hash"] == codes.code_hash(code)
    assert telegram["link"]["purpose"] == "knock"
    wecom = svc.start("u", "p", StartLink(platform="wecom",
                                          connection_id="00000000-0000-0000-0000-000000000001"))
    assert len(wecom["code"]) == 6 and wecom["link"]["connection_id"]
    install = svc.start("u", "p", StartLink(platform="slack"))
    assert install["link"]["purpose"] == "slack_install" and install["install_url"].startswith("https://")
    wait = svc.start("u", "p", StartLink(platform="slack",
                                         connection_id="00000000-0000-0000-0000-000000000002"))
    assert wait["link"]["purpose"] == "slack_wait" and wait["invite"] == "/invite @PuppyOne"
    with pytest.raises(HTTPException) as missing:
        svc.start("u", "p", StartLink(platform="wecom"))
    assert missing.value.status_code == 422


def test_a_platform_that_is_off_cannot_be_started(secrets):
    svc, _ = service({"telegram": SimpleNamespace(start_links=lambda c: {})})
    with pytest.raises(HTTPException) as off:
        svc.start("u", "p", StartLink(platform="slack"))
    assert off.value.status_code == 404


def test_the_wecom_secret_is_sealed_before_it_is_stored(secrets):
    svc, repository = service()
    svc.connect("u", "p", ConnectBot(bot_id=" bot-1 ", secret="not-a-secret"))
    stored = repository.human.call_args.kwargs
    assert stored["external_id"] == "bot-1" and "not-a-secret" not in stored["secret"]


def test_changes_send_only_what_was_set_and_null_means_no_limit(secrets):
    svc, repository = service()
    svc.change("u", "p", "c", ChangeChat(daily_limit_units=None))
    assert repository.human.call_args.kwargs == {"project_id": "p", "conversation_id": "c",
                                                 "daily_limit_units": None}
    with pytest.raises(HTTPException):
        svc.change("u", "p", "c", ChangeChat(enabled=None))


def test_removing_stops_running_runs_as_their_owner(secrets):
    svc, repository = service()
    repository.human.side_effect = lambda action, user, **payload: {
        "owner_user_id": "owner", "stop_runs": ["r1"], "state": "removed"}
    assert svc.remove("manager", "p", "c") == {"owner_user_id": "owner", "state": "removed"}
    svc.agents.command.assert_called_once_with("owner", "r1", "stop")
