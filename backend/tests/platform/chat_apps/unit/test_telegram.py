from __future__ import annotations

import json

import pytest

from src.platform.chat_apps.platforms import build_platforms
from src.platform.chat_apps.platforms.telegram import TelegramPlatform
from src.platform.chat_apps.ports import (
    Message,
    MessageGone,
    NotVerified,
    RateLimited,
    Sent,
    Target,
)

from .platform_support import Calls, FakeClock

SECRET = "s3cret_Token-1"
TOKEN = "123456:ABC-def"
BOT = "PuppyOneBot"


def make(calls: Calls | None = None, clock: FakeClock | None = None) -> TelegramPlatform:
    return TelegramPlatform(
        TOKEN,
        BOT,
        SECRET,
        "https://tg.test",
        transport=(calls or Calls()).transport,
        clock=clock or FakeClock(),
    )


def parse(update: dict):
    return make().parse({}, json.dumps(update).encode())


def user(uid=7, **extra):
    return {"id": uid, "is_bot": False, "first_name": "Alex", "username": "alex", **extra}


def group_message(text, message_id=10, **extra):
    return {
        "message_id": message_id,
        "from": user(),
        "chat": {"id": -100123, "type": "supergroup", "title": "Design team"},
        "date": 1700000000,
        "text": text,
        **extra,
    }


# -- verify -----------------------------------------------------------------


def test_verify_accepts_the_secret_header_in_any_case():
    make().verify({"x-telegram-bot-api-secret-token": SECRET}, b"{}")


@pytest.mark.parametrize("value", ["", SECRET[:-1] + "x", SECRET + "x"])
def test_verify_rejects_a_wrong_secret(value):
    with pytest.raises(NotVerified):
        make().verify({"X-Telegram-Bot-Api-Secret-Token": value}, b"{}")


def test_verify_rejects_a_missing_header():
    with pytest.raises(NotVerified):
        make().verify({}, b"{}")


# -- parse ------------------------------------------------------------------


def test_ask_command_in_a_group_is_a_question_without_the_command():
    [event] = parse({"update_id": 1, "message": group_message(f"/ask@{BOT} what changed?")})
    assert (event.kind, event.via, event.text) == ("question", "message", "what changed?")
    assert event.message_key == "-100123:10"
    assert event.external_chat_id == "-100123"
    assert event.chat_kind == "group"
    assert event.chat_title == "Design team"
    assert event.asker_external_id == "7"
    assert event.external_thread_key == "10"
    assert event.external_event_id == "1"


def test_message_and_guest_message_share_one_message_key():
    [plain] = parse({"update_id": 1, "message": group_message(f"@{BOT} hi")})
    [guest] = parse(
        {"update_id": 2, "guest_message": group_message(f"@{BOT} hi", guest_query_id="GQ1")}
    )
    assert plain.message_key == guest.message_key == "-100123:10"
    assert (plain.via, guest.via) == ("message", "guest")
    assert guest.handle == {"guest_query_id": "GQ1"}
    assert plain.text == guest.text == "hi"


def test_guest_message_in_a_private_chat_is_not_a_dm():
    msg = group_message("what is this", guest_query_id="GQ2")
    msg["chat"] = {"id": 55, "type": "private", "first_name": "Alex"}
    [event] = parse({"update_id": 3, "guest_message": msg})
    assert event.chat_kind == "group"


def test_reply_to_the_bot_is_a_question_and_keys_the_thread():
    reply = {"message_id": 99, "from": {"id": 123456, "is_bot": True, "username": BOT}}
    [event] = parse({"update_id": 1, "message": group_message("and then?", reply_to_message=reply)})
    assert event.kind == "question"
    assert event.text == "and then?"
    assert event.external_thread_key == "99"


def test_reply_to_somebody_else_is_ignored():
    reply = {"message_id": 99, "from": user(uid=8)}
    assert parse({"update_id": 1, "message": group_message("lol", reply_to_message=reply)}) == []


@pytest.mark.parametrize(
    "text",
    [
        "just chatting",
        "/ask what?",  # no @bot in a group: another bot may own /ask
        "/ask@OtherBot what?",
        "/start@OtherBot abc",
        "@OtherBot hi",
        f"hello @{BOT}x",
    ],
)
def test_group_messages_not_addressed_to_us_produce_nothing(text):
    assert parse({"update_id": 1, "message": group_message(text)}) == []


def test_start_with_a_code_is_a_knock_in_a_group_and_in_a_dm():
    [group] = parse({"update_id": 1, "message": group_message(f"/start@{BOT} Zm9v-_bar")})
    assert (group.kind, group.code, group.text) == ("knock", "Zm9v-_bar", "")
    dm = {
        "message_id": 4,
        "from": user(),
        "chat": {"id": 7, "type": "private", "first_name": "Alex"},
        "text": "/start Zm9v-_bar",
    }
    [event] = parse({"update_id": 2, "message": dm})
    assert (event.kind, event.code, event.chat_kind) == ("knock", "Zm9v-_bar", "dm")


def test_start_without_a_code_says_nothing():
    dm = {"message_id": 4, "from": user(), "chat": {"id": 7, "type": "private"}, "text": "/start"}
    assert parse({"update_id": 2, "message": dm}) == []


def test_dm_text_is_a_question_and_new_starts_a_thread():
    dm = {"message_id": 5, "from": user(), "chat": {"id": 7, "type": "private"}, "text": "hello"}
    [question] = parse({"update_id": 1, "message": dm})
    assert (question.kind, question.chat_kind, question.external_thread_key) == (
        "question",
        "dm",
        "dm",
    )
    [fresh] = parse({"update_id": 2, "message": {**dm, "message_id": 6, "text": "/new"}})
    assert (fresh.kind, fresh.external_thread_key) == ("new_thread", "dm")


def test_attachment_without_text_sets_the_flag():
    dm = {
        "message_id": 5,
        "from": user(),
        "chat": {"id": 7, "type": "private"},
        "photo": [{"file_id": "x", "width": 1, "height": 1}],
    }
    [event] = parse({"update_id": 1, "message": dm})
    assert event.has_attachments is True
    assert event.text == ""


def test_messages_from_bots_and_channels_are_dropped():
    bot = group_message(f"@{BOT} hi")
    bot["from"] = {"id": 9, "is_bot": True, "username": "SpamBot"}
    assert parse({"update_id": 1, "message": bot}) == []
    channel = group_message("hi")
    channel["chat"] = {"id": -5, "type": "channel"}
    assert parse({"update_id": 2, "message": channel}) == []


def test_stop_click_carries_the_reply_id_and_callback_handle():
    update = {
        "update_id": 8,
        "callback_query": {
            "id": "4382bfdwdsb323b2d9",
            "from": user(),
            "message": {"message_id": 50, "chat": {"id": -100123, "type": "supergroup"}},
            "chat_instance": "1",
            "data": "stop:reply-42",
        },
    }
    [event] = parse(update)
    assert (event.kind, event.via, event.action_value) == ("stop", "action", "reply-42")
    assert event.message_key == "cb:4382bfdwdsb323b2d9"
    assert event.handle["callback_query_id"] == "4382bfdwdsb323b2d9"
    assert event.external_chat_id == "-100123"
    assert event.asker_external_id == "7"


def test_other_callback_data_is_ignored():
    update = {"update_id": 8, "callback_query": {"id": "1", "from": user(), "data": "other"}}
    assert parse(update) == []


@pytest.mark.parametrize("status", ["left", "kicked"])
def test_removal_of_the_bot_is_a_left_event(status):
    update = {
        "update_id": 9,
        "my_chat_member": {
            "chat": {"id": -100123, "type": "supergroup", "title": "Design team"},
            "from": user(),
            "date": 1700000000,
            "old_chat_member": {"user": {"id": 123456, "is_bot": True}, "status": "member"},
            "new_chat_member": {"user": {"id": 123456, "is_bot": True}, "status": status},
        },
    }
    [event] = parse(update)
    assert (event.kind, event.via, event.external_chat_id) == ("left", "membership", "-100123")
    assert event.message_key == "mcm:-100123:9"


def test_becoming_a_member_is_not_an_event_and_junk_is_ignored():
    update = {
        "update_id": 9,
        "my_chat_member": {
            "chat": {"id": -1, "type": "group"},
            "from": user(),
            "new_chat_member": {"user": {"id": 123456}, "status": "member"},
        },
    }
    assert parse(update) == []
    assert make().parse({}, b"not json") == []
    assert make().parse({}, b"[1]") == []
    assert parse({"update_id": 1, "edited_message": group_message("x")}) == []


# -- links, pacing, settings ------------------------------------------------


def test_start_links():
    assert make().start_links("CODE") == {
        "group": "https://t.me/PuppyOneBot?startgroup=CODE",
        "dm": "https://t.me/PuppyOneBot?start=CODE",
    }


def test_start_links_follow_the_configured_base_for_local_stand_ins():
    platform = TelegramPlatform(
        TOKEN, BOT, SECRET, "https://tg.test", link_base="http://127.0.0.1:9911/"
    )
    assert platform.start_links("C")["group"] == "http://127.0.0.1:9911/PuppyOneBot?startgroup=C"


def test_pacing_groups_edit_every_three_seconds():
    platform = make()
    group = platform.pacing(Target("telegram", "-1"))
    assert (group.min_edit_seconds, group.max_text, group.bytes_limit) == (3.0, 4096, False)
    guest = platform.pacing(Target("telegram", "-1", via="guest"))
    assert guest.min_edit_seconds == 3.0
    dm = platform.pacing(Target("telegram", "7", chat_kind="dm"))
    assert dm.max_text == 4096


def test_build_platforms_builds_only_what_is_enabled():
    class Settings:
        chat_app_platforms = frozenset({"telegram"})
        CHAT_APPS_TELEGRAM_BOT_TOKEN = TOKEN
        CHAT_APPS_TELEGRAM_BOT_USERNAME = BOT
        CHAT_APPS_TELEGRAM_WEBHOOK_SECRET = SECRET
        CHAT_APPS_TELEGRAM_API_BASE = "https://tg.test"
        CHAT_APPS_TELEGRAM_LINK_BASE = "https://t.me"

    built = build_platforms(Settings())
    assert list(built) == ["telegram"]
    assert built["telegram"].name == "telegram"


# -- sending ----------------------------------------------------------------


def sent_reply(method, body):
    return {"ok": True, "result": {"message_id": 77}}


async def test_group_send_replies_to_the_question_with_a_stop_button():
    calls = Calls(sent_reply)
    platform = make(calls)
    target = Target("telegram", "-100123", reply_to="10")
    sent = await platform.send(target, Message("Working…", stop_reply_id="r1"))
    [body] = calls.of("sendMessage")
    assert body["chat_id"] == "-100123"
    assert body["reply_parameters"]["message_id"] == 10
    assert body["reply_markup"] == {
        "inline_keyboard": [[{"text": "Stop", "callback_data": "stop:r1"}]]
    }
    assert sent.message_id == "77"


async def test_group_edit_changes_the_same_message_and_the_final_drops_stop():
    calls = Calls()
    platform = make(calls)
    target = Target("telegram", "-100123")
    sent = Sent("77", {"mode": "chat"})
    await platform.edit(target, sent, Message("half", stop_reply_id="r1"))
    await platform.edit(
        target, sent, Message("done", final=True, stop_reply_id="r1", open_url="https://x.test/a")
    )
    first, last = calls.of("editMessageText")
    assert first["message_id"] == 77 and first["chat_id"] == "-100123"
    assert first["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "stop:r1"
    assert last["reply_markup"] == {
        "inline_keyboard": [[{"text": "Open in PuppyOne", "url": "https://x.test/a"}]]
    }


async def test_text_is_cut_to_4096_characters():
    calls = Calls(sent_reply)
    await make(calls).send(Target("telegram", "-1"), Message("x" * 5000, final=True))
    [body] = calls.of("sendMessage")
    assert len(body["text"]) == 4096
    assert body["text"].endswith("…")


async def test_dm_shows_a_draft_then_sends_the_final_message():
    calls = Calls(sent_reply)
    clock = FakeClock()
    platform = make(calls, clock)
    target = Target("telegram", "7", chat_kind="dm")
    sent = await platform.send(target, Message("Working…"))
    assert calls.methods() == ["sendMessageDraft"]
    assert sent.message_id is None and sent.ref["mode"] == "draft"
    sent = await platform.edit(target, sent, Message("partial answer"))
    drafts = calls.of("sendMessageDraft")
    assert drafts[0]["draft_id"] == drafts[1]["draft_id"] != 0
    assert drafts[1]["text"] == "partial answer"
    final = await platform.edit(target, sent, Message("whole answer", final=True))
    assert calls.methods()[-1] == "sendMessage"
    assert calls.of("sendMessage")[0]["text"] == "whole answer"
    assert final.message_id == "77"
    # after the final message exists, later edits change that message
    await platform.edit(target, final, Message("whole answer!", final=True))
    assert calls.methods()[-1] == "editMessageText"


async def test_guest_answers_the_query_once_then_edits_the_inline_message():
    def reply(method, body):
        if method == "answerGuestQuery":
            return {"ok": True, "result": {"inline_message_id": "IMID"}}
        return {"ok": True, "result": True}

    calls = Calls(reply)
    platform = make(calls)
    target = Target("telegram", "-100123", via="guest", handle={"guest_query_id": "GQ1"})
    sent = await platform.send(target, Message("Working…", stop_reply_id="r1"))
    [answer] = calls.of("answerGuestQuery")
    assert answer["guest_query_id"] == "GQ1"
    assert answer["result"]["input_message_content"] == {"message_text": "Working…"}
    assert sent.message_id == "IMID"
    await platform.edit(target, sent, Message("done", final=True))
    [edit] = calls.of("editMessageText")
    assert edit["inline_message_id"] == "IMID" and "chat_id" not in edit
    assert calls.methods() == ["answerGuestQuery", "editMessageText"]


async def test_edit_with_unchanged_text_is_not_an_error():
    def reply(method, body):
        return (
            400,
            {"ok": False, "error_code": 400, "description": "Bad Request: message is not modified"},
            {},
        )

    sent = Sent("77", {"mode": "chat"})
    assert await make(Calls(reply)).edit(Target("telegram", "-1"), sent, Message("same")) == sent


async def test_429_raises_rate_limited_with_retry_after():
    def reply(method, body):
        return (
            429,
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 7",
                "parameters": {"retry_after": 7},
            },
            {},
        )

    with pytest.raises(RateLimited) as info:
        await make(Calls(reply)).send(Target("telegram", "-1"), Message("x", final=True))
    assert info.value.retry_after == 7


async def test_a_deleted_message_raises_message_gone():
    def reply(method, body):
        return (
            400,
            {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message to edit not found",
            },
            {},
        )

    with pytest.raises(MessageGone):
        await make(Calls(reply)).edit(
            Target("telegram", "-1"), Sent("1", {"mode": "chat"}), Message("x")
        )


async def test_notify_answers_the_callback_privately_else_replies_plainly():
    calls = Calls(sent_reply)
    platform = make(calls)
    await platform.notify(
        Target("telegram", "-1", handle={"callback_query_id": "cb1"}),
        "Only the asker can stop this.",
    )
    assert calls.of("answerCallbackQuery") == [
        {"callback_query_id": "cb1", "text": "Only the asker can stop this.", "show_alert": False}
    ]
    await platform.notify(Target("telegram", "-1", reply_to="10"), "Queued.")
    assert calls.of("sendMessage")[0]["text"] == "Queued."


async def test_leave_leaves_a_group_but_not_a_dm():
    calls = Calls()
    platform = make(calls)
    await platform.leave(Target("telegram", "-1"))
    await platform.leave(Target("telegram", "7", chat_kind="dm"))
    assert calls.of("leaveChat") == [{"chat_id": "-1"}]


async def test_mention_becomes_a_text_mention_entity():
    calls = Calls(sent_reply)
    target = Target("telegram", "-1", asker_external_id="7", reply_to="10")
    await make(calls).send(target, Message("Done · Alex", final=True, mention="Alex"))
    [body] = calls.of("sendMessage")
    assert body["entities"] == [
        {"type": "text_mention", "offset": 7, "length": 4, "user": {"id": 7}}
    ]
