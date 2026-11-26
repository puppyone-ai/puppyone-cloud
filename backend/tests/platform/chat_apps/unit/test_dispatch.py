"""Dispatch: one inbox event and what the database says -> one decision, in design order."""

from datetime import UTC, datetime, timedelta

import pytest

from src.platform.chat_apps.dispatch import clean_label, decide, prompt, request_id, thread_key

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
OWNER = "00000000-0000-0000-0000-00000000000a"


def event(**changes):
    base = {
        "platform": "telegram",
        "message_key": "-100:7",
        "via": "message",
        "kind": "question",
        "state": "pending",
        "external_chat_id": "-100",
        "external_thread_key": "7",
        "asker_external_id": "tg-alex",
        "asker_label": "Alex",
        "created_at": NOW.isoformat(),
        "payload": {"text": "change the price to 29", "chat_kind": "group", "trust": "member"},
    }
    payload = {**base["payload"], **changes.pop("payload", {})}
    return {**base, **changes, "payload": payload}


def chat(**changes):
    return {
        "id": "c1",
        "state": "active",
        "enabled": True,
        "stopped_reason": None,
        "who_can_ask": "owner",
        "owner_user_id": OWNER,
        "owner_external_id": "tg-alex",
        "daily_limit_units": 60,
        "chat_kind": "group",
        "display_name": "Design team",
        **changes,
    }


def situation(**changes):
    return {
        "conversation": chat(),
        "asker_user_id": None,
        "asker_can_run": None,
        "used_today_units": 18,
        "asked_last_hour": 1,
        "notified_last_hour": [],
        "thread": None,
        **changes,
    }


def test_a_question_from_the_owner_is_submitted_as_the_owner():
    decision = decide(event(), situation(), NOW)
    assert decision.action == "submit"
    assert decision.values["request_id"] == str(request_id("telegram", "-100:7"))
    assert decision.values["prompt"] == "[Telegram · Design team · Alex]\nchange the price to 29"
    assert decision.values["thread_key"] == "7"


@pytest.mark.parametrize(
    ("changes", "context", "expected"),
    [
        ({"payload": {"trust": "bot"}}, {}, ("drop", None)),
        ({"payload": {"trust": "external"}}, {}, ("notice", "outsider")),
        ({"payload": {"trust": "guest"}}, {}, ("notice", "outsider")),
        ({}, {"conversation": None}, ("drop", None)),
        ({"via": "guest"}, {"conversation": None}, ("notice", "not_connected")),
        ({"platform": "slack"}, {"conversation": None}, ("notice", "not_connected")),
        ({}, {"conversation": chat(state="asking")}, ("drop", None)),
        ({}, {"conversation": chat(enabled=False)}, ("notice", "paused")),
        ({}, {"conversation": chat(stopped_reason="owner_left")}, ("notice", "paused")),
        ({"asker_external_id": "tg-mia"}, {}, ("notice", "only_owner")),
        ({}, {"used_today_units": 60}, ("notice", "limit")),
        ({}, {"asked_last_hour": 61}, ("notice", "too_many")),
        ({"payload": {"text": "", "has_attachments": True}}, {}, ("notice", "attachments")),
        ({"payload": {"text": "  "}}, {}, ("drop", None)),
    ],
)
def test_checks_stop_at_the_first_that_fails(changes, context, expected):
    decision = decide(event(**changes), situation(**context), NOW)
    assert (decision.action, decision.notice) == expected


def test_people_in_the_project_link_once_then_need_agent_run():
    project = chat(who_can_ask="project")
    unlinked = decide(event(asker_external_id="tg-mia"), situation(conversation=project), NOW)
    assert (unlinked.action, unlinked.notice) == ("hold", "link_account")
    outsider = decide(
        event(asker_external_id="tg-mia"),
        situation(conversation=project, asker_user_id="u2", asker_can_run=False),
        NOW,
    )
    assert outsider.notice == "not_in_project"
    member = decide(
        event(asker_external_id="tg-mia"),
        situation(conversation=project, asker_user_id="u2", asker_can_run=True),
        NOW,
    )
    assert member.action == "submit"


def test_anyone_can_ask_but_strangers_never_can():
    anyone = situation(conversation=chat(who_can_ask="anyone"))
    assert decide(event(asker_external_id="x"), anyone, NOW).action == "submit"
    stranger = event(asker_external_id="x", payload={"trust": "external"})
    assert decide(stranger, anyone, NOW).notice == "outsider"


def test_the_owner_is_also_recognised_by_linked_account():
    context = situation(conversation=chat(owner_external_id=None), asker_user_id=OWNER)
    assert decide(event(), context, NOW).action == "submit"


def test_paused_limit_and_who_can_ask_are_said_once_an_hour():
    context = situation(conversation=chat(enabled=False), notified_last_hour=["paused"])
    assert decide(event(), context, NOW).action == "drop"


def test_a_busy_thread_queues_up_to_five():
    busy = situation(thread={"id": "t", "busy": True, "waiting": 2})
    assert (decide(event(), busy, NOW).action, decide(event(), busy, NOW).notice) == (
        "wait",
        "queued",
    )
    full = situation(thread={"id": "t", "busy": True, "waiting": 5})
    assert decide(event(), full, NOW).notice == "too_many"


def test_a_question_older_than_ten_minutes_is_not_run():
    old = event(created_at=(NOW - timedelta(minutes=11)).isoformat())
    assert decide(old, situation(), NOW).notice == "stale"
    waited = event(created_at=(NOW - timedelta(minutes=11)).isoformat(), state="waiting")
    assert decide(waited, situation(), NOW).action == "submit"


def test_membership_and_buttons():
    assert decide(event(kind="knock", payload={"code": "abc"}), situation(), NOW).values == {
        "code": "abc"
    }
    assert decide(event(kind="left"), situation(), NOW).action == "removed_there"
    assert decide(event(kind="uninstalled"), situation(), NOW).action == "revoke"
    joined = decide(event(kind="joined", platform="slack"), situation(conversation=None), NOW)
    assert joined.values == {"owner": "tg-alex"}
    missing = decide(
        event(kind="joined", asker_external_id=None), situation(conversation=None), NOW
    )
    assert (missing.action, missing.outcome) == ("joined", "inviter_missing")
    stop = decide(event(kind="stop", payload={"action_value": "r1"}), situation(), NOW)
    assert stop.values == {"reply_id": "r1"}


def test_slack_without_inviter_knocks_with_the_next_mention():
    context = situation(conversation=None, join_without_inviter=True)
    decision = decide(event(platform="slack"), context, NOW)
    assert (decision.action, decision.values) == ("knock", {"owner": "tg-alex"})


def test_wecom_mentions_with_a_code_knock_and_without_say_nothing():
    knock = decide(
        event(platform="wecom", payload={"code": "482731"}), situation(conversation=None), NOW
    )
    assert knock.values == {"code": "482731"}
    silent = decide(event(platform="wecom"), situation(conversation=None), NOW)
    assert silent.action == "drop"


def test_idle_dm_and_wecom_threads_start_a_new_session():
    old = (NOW - timedelta(days=2)).isoformat()
    dm = situation(
        conversation=chat(chat_kind="dm"),
        thread={"id": "t", "agent_session_id": "s", "last_activity_at": old},
    )
    assert decide(event(payload={"chat_kind": "dm"}), dm, NOW).values["fresh_session"] is True
    group = situation(thread={"id": "t", "agent_session_id": "s", "last_activity_at": old})
    assert decide(event(), group, NOW).values["fresh_session"] is False


def test_thread_keys():
    assert thread_key(event(external_thread_key=None, payload={"chat_kind": "dm"})) == "dm"
    assert thread_key(event(external_thread_key=None)) == "asker:tg-alex"


def test_message_identity_is_stable():
    assert request_id("telegram", "-100:7") == request_id("telegram", "-100:7")
    assert request_id("telegram", "-100:7") != request_id("slack", "-100:7")


def test_the_prompt_label_cannot_be_forged():
    assert clean_label("Design\nteam]\x07 [Owner") == "Design team)  (Owner"
    assert len(clean_label("x" * 200)) == 64
    assert prompt("wecom", "群\n", None, "hi").startswith("[WeCom · 群 · someone]\n")


def test_a_knocking_chat_is_named_by_its_title_or_its_person():
    from src.platform.chat_apps.gateway import chat_name

    assert chat_name("telegram", {"chat_title": "Design team"}, "Alex") == "Design team"
    assert chat_name("telegram", {"chat_kind": "dm"}, "Alex") == "Alex"
    assert chat_name("slack", {"chat_kind": "dm"}, None) == "Direct message"
    assert chat_name("wecom", {"chat_kind": "group"}, "enc-alex") == "WeCom group"


def test_new_in_a_direct_chat_starts_over_and_says_so():
    d = decide(event(kind="new_thread"), situation(), NOW)
    assert (d.action, d.notice, d.values["thread_key"]) == ("new_thread", "new_thread", "dm")
