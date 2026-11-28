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


def test_waking_a_project_happens_once_a_minute_and_never_fails_the_question():
    calls = []

    async def wake(user, project):
        calls.append((user, project))
        if project == "p-broken":
            raise RuntimeError("provider down")

    gateway = Gateway(Repository(), None, {}, prewake=wake)

    async def run():
        for project in ("p1", "p1", "p2", "p-broken"):
            gateway._wake({"project_id": project, "owner_user_id": "u1"})
        await asyncio.gather(*gateway._wakes)

    asyncio.run(run())
    assert calls == [("u1", "p1"), ("u1", "p2"), ("u1", "p-broken")]
    assert gateway.stats["prewoken"] == 2 and gateway.stats["prewake_failed"] == 1
