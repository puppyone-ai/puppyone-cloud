"""An Agent's machine is asked for as soon as the Agent is let in, never at its owner's expense."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest

from src.config import settings
from src.platform.access import agent_links
from src.platform.access.agent_links import AgentLinkService
from src.platform.access.lifecycle_schemas import AgentConnect

KEY = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
CONNECTION = "11111111-1111-4111-8111-111111111111"
ENDPOINT = {
    "host": "gateway.fixture",
    "port": 2222,
    "known_hosts": "gateway.fixture ssh-ed25519 AAAA",
}


def service(state="retired", decided="approved", failing=None):
    machine = {"id": "agent-machine", "user_id": "owner", "project_id": "p", "state": state}

    def call(name, **_):
        if name == "access_agent_workspace":
            return machine
        if name == "access_agent_connect":
            return {"id": CONNECTION, "state": decided, "expires_at": "2099-01-01T00:00:00Z"}
        raise AssertionError(name)

    repository = SimpleNamespace(
        call=Mock(side_effect=call),
        command=Mock(return_value={"id": CONNECTION, "state": decided}),
        admits_without_sign_in=Mock(return_value=False),
    )
    workspaces = SimpleNamespace(request_open=AsyncMock(side_effect=failing))
    links = AgentLinkService(repository, endpoint=lambda: ENDPOINT, workspaces=workspaces)
    return links, workspaces, machine


async def prepared():
    await asyncio.gather(*list(agent_links._PREPARING))


async def test_allowing_an_agent_asks_for_its_machine_at_once():
    links, workspaces, machine = service()
    assert (await links.decide("owner", CONNECTION, "approve"))["state"] == "approved"
    await prepared()
    workspaces.request_open.assert_awaited_once_with("owner", "p", machine)


async def test_a_rejected_agent_gets_no_machine():
    links, workspaces, _ = service(decided="rejected")
    await links.decide("owner", CONNECTION, "reject")
    await prepared()
    workspaces.request_open.assert_not_called()


@pytest.mark.parametrize("state", ["running", "paused", "resuming"])
async def test_a_machine_already_there_is_left_alone(state):
    links, workspaces, _ = service(state=state)
    await links.decide("owner", CONNECTION, "approve")
    await prepared()
    workspaces.request_open.assert_not_called()


async def test_an_agent_admitted_by_its_link_also_gets_its_machine(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://web.fixture")
    monkeypatch.setattr(settings, "AGENT_OPEN_LINKS_ENABLED", True)
    links, workspaces, machine = service()
    request = AgentConnect(invitation="pai_" + "a" * 43, name="Codex", public_key=KEY)
    assert (await links.connect(request))["status"] == "ready"
    await prepared()
    workspaces.request_open.assert_awaited_once_with("owner", "p", machine)


async def test_a_machine_that_cannot_be_started_never_fails_the_owner(caplog):
    links, _, _ = service(failing=RuntimeError("provider unavailable"))
    assert (await links.decide("owner", CONNECTION, "approve"))["state"] == "approved"
    await prepared()
    assert "agent_workspace_prepare_failed" in caplog.text


def test_an_agent_chooses_its_picture_from_the_list_and_anything_else_is_no_choice():
    def connect(avatar):
        return AgentConnect(
            invitation="pai_" + "a" * 43, name="Codex", public_key=KEY, avatar=avatar
        )

    assert connect("grok-bot").avatar == "grok-bot"
    assert connect("  Claude ").avatar == "claude"
    assert connect("other").avatar == "other"
    # Never a refusal: an Agent that misreads the list still connects, with no picture.
    for avatar in ("YOUR AVATAR", "https://example.com/me.png", "<svg/>", "x" * 500, 7, None):
        assert connect(avatar).avatar is None
    assert AgentConnect(invitation="pai_" + "a" * 43, name="Codex", public_key=KEY).avatar is None


def test_the_instructions_ask_for_exactly_one_picture_from_the_list(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://api.fixture")
    text = agent_links.agent_instructions("pai_" + "a" * 43)
    assert '\\"avatar\\":\\"YOUR AVATAR\\"' in text
    assert ", ".join(agent_links.AGENT_AVATARS) in text
    assert agent_links.AGENT_AVATARS[-1] == "other"


async def test_connecting_records_the_chosen_picture(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://web.fixture")
    monkeypatch.setattr(settings, "AGENT_OPEN_LINKS_ENABLED", True)
    links, _, _ = service(decided="pending")
    request = AgentConnect(
        invitation="pai_" + "a" * 43, name="Codex", public_key=KEY, avatar="codex"
    )
    await links.connect(request)
    name, values = links.repository.call.call_args.args[0], links.repository.call.call_args.kwargs
    assert name == "access_agent_connect"
    assert values["agent_name"] == "Codex" and values["agent_avatar"] == "codex"
