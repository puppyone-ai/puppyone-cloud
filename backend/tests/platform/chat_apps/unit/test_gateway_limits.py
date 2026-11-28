"""One gateway process holds at most CHAT_APPS_WECOM_MAX_CONNECTIONS WeCom
long connections, so a second instance gets the rest."""

import asyncio

from src.platform.chat_apps.gateway import Gateway


class Repository:
    def __init__(self):
        self.claims = 0

    def gateway(self, action, worker, **payload):
        assert action == "claim_connection"
        self.claims += 1
        return None


def test_a_full_gateway_claims_no_more_wecom_connections():
    repository = Repository()
    gateway = Gateway(repository, None, {"wecom": object()}, max_wecom=2)

    async def run(held):
        return await gateway.connection_once(asyncio.Event(), held)

    assert asyncio.run(run({"a"})) is False and repository.claims == 1
    assert asyncio.run(run({"a", "b"})) is False and repository.claims == 1
