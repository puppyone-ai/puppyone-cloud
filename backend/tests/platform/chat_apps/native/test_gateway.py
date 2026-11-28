"""The whole path on real PostgreSQL and PostgREST: a fake chat platform, the
real AgentService, and a fake executor that moves Runs as agent_worker would.

One question -> one message (starting -> working -> answer); redelivery and
Telegram's two event kinds -> one Run; a restarted gateway edits the same
message; approval waits are shown; an owner who left gets "paused".
"""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from src.config import settings
from src.platform.agent.runtime.control.admission import Admission
from src.platform.agent.runtime.persistence.commands import RunRepository
from src.platform.agent.service import AgentService
from src.platform.authorization.factory import build_authorization_service
from src.platform.chat_apps.gateway import Gateway
from src.platform.chat_apps.ports import Inbound
from src.platform.chat_apps.repository import ChatAppRepository
from src.platform.chat_apps.webhook_router import store
from tests.platform.chat_apps.native.support import member, project
from tests.platform.chat_apps.unit.support import FakePlatform


@pytest.fixture(scope="module")
def client(db):
    from postgrest import SyncPostgrestClient

    from src.infra.supabase.instrumentation import DatabaseTransport
    from tests.agent.runtime.owned_http import owned_http_service

    with (
        owned_http_service("postgrest", database_url=db.url) as url,
        httpx.Client(trust_env=False, transport=DatabaseTransport(path_prefix="/")) as http,
    ):
        yield SyncPostgrestClient(url, http_client=http)


@pytest.fixture
def world(db, client, monkeypatch):
    monkeypatch.setattr(settings, "MANAGED_AI_ENABLED", True)
    monkeypatch.setattr(settings, "CLOUD_AGENT_DEFAULT_MODEL", "fixture-model")
    monkeypatch.setattr(settings, "CHAT_APPS_CODE_HASH_SECRET", "chat-code-hash-test-placeholder")
    project_id, owner, org = project(db)
    # Earlier tests' pending rows must not leak into this one.
    db.sql(
        "UPDATE chat_app_inbox SET state='done' WHERE state<>'done';"
        "UPDATE chat_app_replies SET state='done' WHERE state IN ('pending','active');"
    )
    db.sql(f"""INSERT INTO public.access_surfaces(id,project_id,org_id,kind,name,created_by)
               VALUES ('{uuid4()}','{project_id}','{org}','agent','Agent','{owner}')""")
    chat = f"-{uuid4().int % 10**9}"
    repository = ChatAppRepository(client)
    runs = RunRepository(client)

    class Workbenches:  # the interactive workspace exists; machines are the executor's
        def admit(self, context, identity=None):
            return {"id": None, "target_ref": "refs/heads/main"}

    agents = AgentService(
        runs, Admission(runs, build_authorization_service(client)), workbenches=Workbenches()
    )
    platform = FakePlatform()
    from src.platform.chat_apps import codes

    repository.human(
        "start_link",
        owner,
        project_id=project_id,
        platform="telegram",
        purpose="knock",
        code_hash=codes.code_hash(f"knock{chat}"),
    )
    knocked = repository.gateway(
        "knock",
        "test",
        platform="telegram",
        code_hash=codes.code_hash(f"knock{chat}"),
        external_chat_id=chat,
        chat_kind="group",
        display_name="Design team",
        owner_external_id="tg-alex",
    )
    repository.human(
        "decide",
        owner,
        project_id=project_id,
        conversation_id=knocked["conversation_id"],
        decision="allow",
    )

    def gateway(name="gateway-a"):
        return Gateway(
            repository,
            agents,
            {"telegram": platform},
            worker=name,
            web_origin="https://web.example.test",
            mentions={"telegram": "@PuppyOneBot"},
        )

    def ask(
        message_id,
        text="change the price to 29",
        via="message",
        asker="tg-alex",
        reply_to=None,
        handle=None,
    ):
        event = Inbound(
            platform="telegram",
            message_key=f"{chat}:{message_id}",
            via=via,
            kind="question",
            external_chat_id=chat,
            asker_external_id=asker,
            asker_label="Alex" if asker == "tg-alex" else "Mia",
            text=text,
            message_id=str(message_id),
            external_thread_key=str(reply_to or message_id),
            handle=handle or {},
        )
        return store(repository, [event])

    def drain(g):
        async def run():
            for _ in range(50):
                if not (await g.inbox_once() or await g.reply_once()):
                    return
            raise AssertionError(f"gateway did not settle: {dict(g.stats)}")

        asyncio.run(run())

    drain(gateway())  # says hello in the chat

    def runs_of_project():
        return json.loads(
            db.sql(
                "SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY created_at),'[]') FROM agent_runs r "
                f"WHERE project_id='{project_id}'"
            )
        )

    def move(run_id, state, text=None, tool=None):
        db.sql(
            f"UPDATE agent_runs SET state='{state}', updated_at=now()"
            + (f", snapshot=snapshot||'{json.dumps({'text': text})}'::jsonb" if text else "")
            + f" WHERE id='{run_id}'"
        )
        if tool:
            db.sql(
                "INSERT INTO agent_run_tools(run_id,call_id,name,input,state) VALUES "
                f"('{run_id}','{uuid4()}','{tool[0]}','{json.dumps(tool[1])}'::jsonb,'{tool[2]}')"
            )
        db.sql("UPDATE chat_app_replies SET next_attempt_at=NULL")

    return locals()


def test_one_question_becomes_one_message_that_ends_as_the_answer(world, db):
    w = world
    g = w["gateway"]()
    assert w["ask"](11) == 1
    w["drain"](g)
    welcome = w["platform"].sent[0][1].text
    assert w["platform"].sent[0][1].final  # never a draft or an open stream
    assert len(w["platform"].sent) == 2
    assert welcome.startswith("I'm in. Reply to me or send /ask@PuppyOneBot to ask.")
    [run] = w["runs_of_project"]()
    assert run["prompt"] == "[Telegram · Design team · Alex]\nchange the price to 29"
    assert run["user_id"] == w["owner"]
    assert w["platform"].sent[-1][1].text == "Starting up…"
    w["move"](
        run["id"], "running", tool=("edit", {"path": "/workspace/repo/pricing.md"}, "executing")
    )
    w["drain"](g)
    assert w["platform"].edits[-1][1].text == "Editing pricing.md…"
    w["move"](run["id"], "succeeded", text="Monthly is 29 now.")
    w["drain"](g)
    final = w["platform"].edits[-1][1]
    assert final.final and final.text == "Monthly is 29 now.\n\nChanged 1 file: pricing.md"
    assert final.open_url.startswith(
        f"https://web.example.test/open/projects/{w['project_id']}?session="
    )
    assert db.sql(f"SELECT units FROM chat_app_replies WHERE run_id='{run['id']}'") == "1"


def test_redelivery_and_both_telegram_kinds_make_one_run(world):
    w = world
    g = w["gateway"]()
    assert w["ask"](21) == 1
    assert w["ask"](21) == 0
    assert w["ask"](21, via="guest") == 0
    w["drain"](g)
    assert len(w["runs_of_project"]()) == 1


def test_a_guest_mode_answer_goes_back_through_its_guest_query(world):
    w = world
    g = w["gateway"]()
    w["ask"](25, via="guest", handle={"guest_query_id": "gq-25"})
    w["drain"](g)
    [run] = w["runs_of_project"]()
    w["move"](run["id"], "succeeded", text="Monthly is 29 now.")
    w["drain"](g)
    target, _ = w["platform"].sent[-1]
    assert target.handle["guest_query_id"] == "gq-25"
    assert w["platform"].edits[-1][1].final


def test_a_long_answer_pings_the_asker_with_a_whole_message(world, db):
    w = world
    g = w["gateway"]()
    w["ask"](95)
    w["drain"](g)
    [run] = w["runs_of_project"]()
    db.sql(f"UPDATE agent_runs SET created_at=now()-interval '3 minutes' WHERE id='{run['id']}'")
    w["move"](run["id"], "succeeded", text="Monthly is 29 now.")
    w["drain"](g)
    ping = w["platform"].sent[-1][1]
    assert ping.text == "Done · Alex" and ping.mention == "tg-alex"
    assert ping.final  # a WeCom stream left open spins forever


def test_stop_on_a_finished_answer_stops_nothing_and_says_so(world, db):
    w = world
    g = w["gateway"]()
    settled = []

    async def settle_stop(target, reply_id, title):
        settled.append((reply_id, title))

    w["platform"].settle_stop = settle_stop  # a platform whose Stop card changes when pressed
    w["ask"](91)
    w["drain"](g)
    [run] = w["runs_of_project"]()
    w["move"](run["id"], "succeeded", text="Monthly is 29 now.")
    w["drain"](g)
    reply = db.sql(f"SELECT id FROM chat_app_replies WHERE run_id='{run['id']}'")
    store(
        w["repository"],
        [
            Inbound(
                platform="telegram",
                message_key=f"cb:{uuid4()}",
                via="action",
                kind="stop",
                external_chat_id=w["chat"],
                asker_external_id="tg-alex",
                action_value=reply,
                handle={"req_id": "E1"},
            )
        ],
    )
    w["drain"](g)
    assert settled == [(reply, "Already finished.")]
    [run] = w["runs_of_project"]()
    assert run["state"] == "succeeded" and not run["stop_requested"]


def test_a_new_gateway_keeps_editing_the_same_message(world, db):
    w = world
    first = w["gateway"]("gateway-a")
    w["ask"](31)
    w["drain"](first)
    [run] = w["runs_of_project"]()
    message = w["platform"].sent[-1]
    w["move"](run["id"], "running", text="Half way")
    # gateway-a dies holding the reply's lease; it expires.
    asyncio.run(first._db("claim_reply", lease_seconds=30))
    db.sql("UPDATE chat_app_replies SET lease_expires_at=now()-interval '1 second'")
    second = w["gateway"]("gateway-b")
    sent_before = len(w["platform"].sent)
    w["drain"](second)
    assert len(w["platform"].sent) == sent_before
    assert w["platform"].edits[-1][0] == f"m{w['platform'].sent.index(message) + 1}"
    assert w["platform"].edits[-1][1].text == "Half way"


def test_waiting_for_approval_is_shown_and_continues_after(world):
    w = world
    g = w["gateway"]()
    w["ask"](41)
    w["drain"](g)
    [run] = w["runs_of_project"]()
    w["move"](
        run["id"], "waiting_approval", tool=("bash", {"command": "git reset --hard"}, "waiting")
    )
    w["drain"](g)
    waiting = w["platform"].edits[-1][1]
    assert waiting.text == "Waiting for Alex to approve in PuppyOne: bash git reset --hard"
    assert waiting.button is None and waiting.stop_reply_id
    w["move"](run["id"], "running", text="Resetting")
    w["drain"](g)
    assert w["platform"].edits[-1][1].text == "Resetting"


def test_a_second_question_in_the_thread_waits_its_turn(world):
    w = world
    g = w["gateway"]()
    w["ask"](51)
    w["drain"](g)
    w["ask"](52, text="and the yearly price?", reply_to=51)
    w["drain"](g)
    assert w["platform"].sent[-1][1].text == "I'll get to this next."
    assert len(w["runs_of_project"]()) == 1
    [run] = w["runs_of_project"]()
    w["move"](run["id"], "succeeded", text="Done")
    w["drain"](g)
    assert len(w["runs_of_project"]()) == 2
    # The queued message became the working message (edited, not a new one).
    assert w["platform"].edits[-1][1].text == "Starting up…"


def test_the_owner_left_so_the_next_question_gets_paused(world, db):
    w = world
    g = w["gateway"]()
    other = member(db, w["project_id"], w["org"], "admin")
    db.sql(
        f"UPDATE chat_app_conversations SET owner_user_id='{other}' "
        f"WHERE project_id='{w['project_id']}'"
    )
    db.sql(f"UPDATE project_members SET role='viewer' WHERE user_id='{other}'")
    db.sql(
        f"UPDATE chat_app_conversations SET owner_external_id='tg-alex' WHERE project_id='{w['project_id']}'"
    )
    w["ask"](61)
    w["drain"](g)
    assert w["platform"].sent[-1][1].text.startswith("I'm paused here.")
    assert w["runs_of_project"]() == []


def test_strangers_and_unlinked_people_do_not_start_runs(world, db):
    w = world
    g = w["gateway"]()
    w["ask"](71, asker="tg-mia")
    w["drain"](g)
    assert w["platform"].sent[-1][1].text == "Only Alex can ask me here."
    db.sql(
        f"UPDATE chat_app_conversations SET who_can_ask='project' WHERE project_id='{w['project_id']}'"
    )
    w["ask"](72, asker="tg-mia")
    w["drain"](g)
    link = w["platform"].sent[-1][1]
    assert link.text == "Link your PuppyOne account to ask."
    assert link.button[1].startswith("https://web.example.test/chat-apps/link?code=")
    assert w["runs_of_project"]() == []


def test_stop_only_for_the_asker_or_owner(world, db):
    w = world
    g = w["gateway"]()
    w["ask"](81)
    w["drain"](g)
    reply = db.sql(
        "SELECT r.id FROM chat_app_replies r JOIN chat_app_conversations c "
        f"ON c.id=r.conversation_id WHERE r.run_id IS NOT NULL AND c.project_id='{w['project_id']}'"
    )
    click = Inbound(
        platform="telegram",
        message_key=f"cb:{uuid4()}",
        via="action",
        kind="stop",
        external_chat_id=w["chat"],
        asker_external_id="tg-mia",
        asker_label="Mia",
        action_value=reply,
        handle={"callback_query_id": "q1"},
    )
    store(w["repository"], [click])
    w["drain"](g)
    assert w["platform"].notices[-1][1] == "Only Alex can stop this."
    store(
        w["repository"],
        [
            Inbound(
                **{**click.__dict__, "message_key": f"cb:{uuid4()}", "asker_external_id": "tg-alex"}
            )
        ],
    )
    w["drain"](g)
    [run] = w["runs_of_project"]()
    assert run["stop_requested"] is True or run["state"] == "stopped"
    assert w["platform"].notices[-1][1] == "Stopping…"


def test_a_used_or_unknown_code_gets_a_reply_instead_of_silence(world):
    w = world
    g = w["gateway"]()

    def knock(message_id, chat, code):
        store(
            w["repository"],
            [
                Inbound(
                    platform="telegram",
                    message_key=f"{chat}:{message_id}",
                    via="message",
                    kind="knock",
                    code=code,
                    external_chat_id=chat,
                    chat_kind="dm",
                    asker_external_id="tg-alex",
                    asker_label="Alex",
                    message_id=str(message_id),
                )
            ],
        )
        w["drain"](g)
        return w["platform"].sent[-1][1].text

    # The setup's code was used when the group knocked; a QR scanned after
    # that, or one that never existed, must not look ignored.
    assert knock(81, "tg-dm-1", f"knock{w['chat']}") == (
        "This link has expired or was already used. "
        "Open PuppyOne → Agents → Add to chat for a new one."
    )
    assert knock(82, "tg-dm-2", "never-issued").startswith("This link has expired")
    # The group already in the project scans a fresh code again.
    from src.platform.chat_apps import codes

    w["repository"].human(
        "start_link",
        w["owner"],
        project_id=w["project_id"],
        platform="telegram",
        purpose="knock",
        code_hash=codes.code_hash("fresh"),
    )
    assert knock(83, w["chat"], "fresh") == "This chat is already added to PuppyOne."


def test_only_a_question_that_will_run_wakes_the_workspace(world):
    w = world
    g = w["gateway"]()
    woken = []

    async def wake(user, project):
        woken.append((user, project))

    g.prewake = wake
    w["ask"](91, asker="tg-mia")  # not allowed to ask here: nothing wakes
    w["drain"](g)
    assert woken == []
    w["ask"](92)
    w["drain"](g)
    assert woken == [(w["owner"], w["project_id"])]
