"""What an Agent is told: the page it follows as written, and the ready response it acts on."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.config import settings
from src.platform.access import agent_links
from src.platform.access.agent_links import AgentLinkService, https_access
from src.platform.access.lifecycle_schemas import AgentConnect

from .support import new_key, public_text

CONNECTION = "11111111-1111-4111-8111-111111111111"
ENDPOINT = {"host": "gateway.fixture", "port": 2222, "known_hosts": "[gateway.fixture]:2222 key"}


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://api.fixture/")
    return agent_links.agent_instructions("pai_" + "a" * 43)


def fenced(page):
    return [block for block in page.split("```")[1::2]]


def test_the_page_carries_the_helper_exactly_as_the_server_expects_it(page):
    (block,) = fenced(page)
    opening, _, rest = block.strip("\n").partition("\n")
    assert opening == (
        "mkdir -p ~/.puppyone && cat > ~/.puppyone/run <<'PUPPYONE_RUN' && chmod 700 ~/.puppyone/run"
    )
    assert rest == agent_links.RUN_HELPER + "PUPPYONE_RUN"
    # The helper signs exactly the message the server checks, under its namespace.
    assert "puppyone-agent-exec-v1\\n%s\\n%s\\n" in agent_links.RUN_HELPER
    assert "-n puppyone-agent" in agent_links.RUN_HELPER


def test_https_comes_first_and_ssh_is_the_alternative(page):
    assert "It needs `ssh-keygen` and `curl`" in page
    assert "go to step 4 with its `https` object" in page
    assert page.index("Run `https.install` once") < page.index("### Or over SSH")
    assert "kex_exchange_identification" in page
    assert "puppyone_proxy" not in page and "ProxyCommand" not in page
    # Every indented line is a command an Agent runs as written.
    commands = [line.strip() for line in page.splitlines() if line.startswith("    ")]
    assert "~/.puppyone/run 'pwd && ls'" in commands


def test_https_access_names_the_exec_url_and_installs_it_once(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://api.fixture/")
    access = https_access(CONNECTION)
    url = f"https://api.fixture/api/v1/agent-links/connections/{CONNECTION}/exec"
    assert access == {
        "url": url,
        "install": (
            f"mkdir -p ~/.puppyone && printf '%s\\n' '{CONNECTION}' '{url}' > ~/.puppyone/connection"
        ),
        "command": "~/.puppyone/run",
    }


def test_https_access_is_not_offered_without_a_public_address(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "")
    with pytest.raises(Exception) as caught:
        https_access(CONNECTION)
    assert getattr(caught.value, "status_code", None) == 503


async def test_polling_an_allowed_connection_returns_https_and_ssh(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://api.fixture")
    repository = SimpleNamespace(
        call=Mock(
            return_value={
                "id": CONNECTION,
                "state": "approved",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        )
    )
    links = AgentLinkService(repository, endpoint=lambda: ENDPOINT)
    ready = await links.status(CONNECTION, "pac_" + "b" * 43)
    assert ready["status"] == "ready"
    assert ready["expires_at"] == "2099-01-01T00:00:00Z"
    assert ready["https"]["url"].endswith(f"/connections/{CONNECTION}/exec")
    assert ready["ssh"]["user"] == f"agent-{CONNECTION}"


async def test_a_pending_poll_offers_no_way_in_yet(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://api.fixture")
    repository = SimpleNamespace(call=Mock(return_value={"id": CONNECTION, "state": "pending"}))
    links = AgentLinkService(repository, endpoint=lambda: ENDPOINT)
    waiting = await links.status(CONNECTION, "pac_" + "b" * 43)
    assert waiting == {"status": "pending", "poll_interval": agent_links.POLL_INTERVAL_SECONDS}


async def test_a_deployment_without_a_public_address_records_no_connection(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://web.fixture")
    monkeypatch.setattr(settings, "PUBLIC_URL", "")
    repository = SimpleNamespace(call=Mock(), admits_without_sign_in=Mock(return_value=False))
    links = AgentLinkService(repository, endpoint=lambda: ENDPOINT)
    request = AgentConnect(
        invitation="pai_" + "a" * 43, name="Codex", public_key=public_text(new_key())
    )
    with pytest.raises(Exception) as caught:
        await links.connect(request)
    assert getattr(caught.value, "status_code", None) == 503
    repository.call.assert_not_called()
