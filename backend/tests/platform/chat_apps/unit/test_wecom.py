from __future__ import annotations

import asyncio
import json

import pytest

from src.platform.chat_apps.platforms import build_platforms
from src.platform.chat_apps.platforms.wecom import WeComPlatform
from src.platform.chat_apps.ports import (
    Message,
    NotHere,
    NotVerified,
    RateLimited,
    Sent,
    Target,
)

from .platform_support import FakeClock, FakeNetwork, FakeSocket, until

BOT = "aibotid-1"
BEAT = 7.0  # distinct from every backoff step, so the fake sleeper can tell them apart


class Sleeper:
    """Backoff sleeps return at once; heartbeat sleeps wait for ``tick``."""

    def __init__(self):
        self.delays: list[float] = []
        self._waiting: list[asyncio.Future] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        if seconds != BEAT:
            await asyncio.sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        self._waiting.append(future)
        await future

    def tick(self) -> None:
        waiting, self._waiting = self._waiting, []
        for future in waiting:
            if not future.done():
                future.set_result(None)

    def backoffs(self) -> list[float]:
        return [d for d in self.delays if d != BEAT]


def not_acking(frame):
    """Acks the subscription only: heartbeats go unanswered."""
    return {"errcode": 0, "errmsg": "ok"} if frame["cmd"] == "aibot_subscribe" else None


class Harness:
    def __init__(self, *sockets: FakeSocket):
        self.net = FakeNetwork(*sockets)
        self.sleeper = Sleeper()
        self.clock = FakeClock()
        self.platform = WeComPlatform(
            "wss://ws.test",
            connect=self.net.connect,
            clock=self.clock,
            sleep=self.sleeper,
            heartbeat_seconds=BEAT,
            ack_timeout=0.02,
        )
        self.stop = asyncio.Event()
        self.delivered: list = []
        self.beats = 0
        self.beat_answer = True
        self.task: asyncio.Task | None = None

    async def _deliver(self, inbound):
        self.delivered.append(inbound)

    async def _heartbeat(self) -> bool:
        self.beats += 1
        return self.beat_answer

    def start(self) -> None:
        self.task = asyncio.ensure_future(
            self.platform.hold(BOT, "the-secret", self._deliver, self._heartbeat, self.stop)
        )

    async def live(self) -> None:
        await until(lambda: BOT in self.platform._live)

    async def finish(self) -> str:
        self.stop.set()
        self.sleeper.tick()
        assert self.task is not None
        return await asyncio.wait_for(self.task, 2)


def target(**extra) -> Target:
    base = {
        "account_id": BOT,
        "credential": "the-secret",
        "handle": {"req_id": "R1"},
    }
    base.update(extra)
    return Target("wecom", "CHAT1", **base)


def msg_frame(body: dict, req_id="R1", cmd="aibot_msg_callback") -> dict:
    return {"cmd": cmd, "headers": {"req_id": req_id}, "body": body}


GROUP_TEXT = {
    "msgid": "MSG1",
    "aibotid": BOT,
    "chatid": "CHAT1",
    "chattype": "group",
    "from": {"userid": "USER1"},
    "msgtype": "text",
    "text": {"content": "@RobotA 482731"},
}


# -- unused HTTP side ---------------------------------------------------------


def test_http_side_is_unused():
    platform = WeComPlatform()
    with pytest.raises(NotVerified):
        platform.verify({}, b"")
    assert platform.parse({}, b"{}") == []
    assert platform.handshake({}, b"{}") is None


def test_pacing_is_in_bytes_with_a_nine_minute_stream():
    pacing = WeComPlatform().pacing(target())
    assert (pacing.min_edit_seconds, pacing.max_text, pacing.bytes_limit) == (2.0, 20480, True)
    assert pacing.stream_seconds == 330


def test_build_platforms_wires_the_ws_url():
    class Settings:
        chat_app_platforms = frozenset({"wecom", "unknown-ignored"})
        CHAT_APPS_WECOM_WS_URL = "wss://openws.work.weixin.qq.com"

    assert list(build_platforms(Settings())) == ["wecom"]


# -- subscribe, heartbeat, reconnect ------------------------------------------


async def test_subscribe_sends_bot_id_and_secret_and_makes_the_socket_live():
    sock = FakeSocket()
    h = Harness(sock)
    h.start()
    await h.live()
    subscribe = sock.sent[0]
    assert subscribe["cmd"] == "aibot_subscribe"
    assert subscribe["body"] == {"bot_id": BOT, "secret": "the-secret"}
    assert subscribe["headers"]["req_id"]
    assert await h.finish() == "stopped"
    assert sock.closed and BOT not in h.platform._live


async def test_a_rejected_subscription_returns_failed_without_retrying():
    sock = FakeSocket(ack=lambda f: {"errcode": 40013, "errmsg": "invalid secret"})
    h = Harness(sock)
    assert (
        await asyncio.wait_for(h.platform.hold(BOT, "bad", h._deliver, h._heartbeat, h.stop), 2)
        == "failed"
    )
    assert h.net.connects == 1 and BOT not in h.platform._live


async def test_each_heartbeat_pings_and_calls_the_lease_callback():
    sock = FakeSocket()
    h = Harness(sock)
    h.start()
    await h.live()
    for expected in (1, 2):
        h.sleeper.tick()
        await until(lambda e=expected: h.beats == e and sock.cmds().count("ping") == e)
    assert h.net.connects == 1
    assert await h.finish() == "stopped"


async def test_losing_the_lease_stops_the_hold():
    sock = FakeSocket()
    h = Harness(sock)
    h.beat_answer = False
    h.start()
    await h.live()
    h.sleeper.tick()
    assert await asyncio.wait_for(h.task, 2) == "stopped"
    assert sock.closed and h.net.connects == 1


async def test_two_missing_acks_reconnect_with_backoff():
    first, second = FakeSocket(ack=not_acking), FakeSocket()
    h = Harness(first, second)
    h.start()
    await h.live()
    h.sleeper.tick()
    await until(lambda: first.cmds().count("ping") == 1)
    await asyncio.sleep(0.05)  # first ack times out
    assert h.net.connects == 1  # one miss is not enough
    h.sleeper.tick()
    await until(lambda: h.net.connects == 2)
    assert first.closed
    assert h.sleeper.backoffs()[0] == 2.0
    await until(lambda: second.cmds() == ["aibot_subscribe"] and BOT in h.platform._live)
    assert await h.finish() == "stopped"


async def test_a_dropped_connection_reconnects_and_backoff_grows_while_refused():
    first = FakeSocket()
    h = Harness(first)
    h.start()
    await h.live()
    first.drop()
    await until(lambda: len(h.sleeper.backoffs()) >= 5)
    assert h.sleeper.backoffs()[:5] == [2.0, 5.0, 10.0, 30.0, 60.0]
    assert h.net.connects >= 2
    assert await h.finish() == "stopped"


async def test_displaced_returns_without_reconnecting():
    sock = FakeSocket()
    h = Harness(sock, FakeSocket())
    h.start()
    await h.live()
    sock.push(
        {
            "cmd": "aibot_event_callback",
            "headers": {"req_id": "E1"},
            "body": {
                "msgid": "m",
                "create_time": 1,
                "aibotid": BOT,
                "msgtype": "event",
                "event": {"eventtype": "disconnected_event"},
            },
        }
    )
    assert await asyncio.wait_for(h.task, 2) == "displaced"
    assert h.net.connects == 1 and sock.closed
    assert h.delivered == []


# -- inbound ------------------------------------------------------------------


async def delivered_from(*frames: dict) -> list:
    sock = FakeSocket()
    h = Harness(sock)
    h.start()
    await h.live()
    for frame in frames:
        sock.push(frame)
    await asyncio.sleep(0.02)
    await h.finish()
    return h.delivered


async def test_group_mention_with_a_code_sets_the_code_and_keeps_the_text():
    [event] = await delivered_from(msg_frame(GROUP_TEXT, req_id="R7"))
    assert event.message_key == "MSG1" and event.kind == "question" and event.via == "message"
    assert event.external_chat_id == "CHAT1" and event.chat_kind == "group"
    assert event.account_id == BOT and event.asker_external_id == "USER1"
    assert event.text == "482731" and event.code == "482731"
    assert event.handle == {"req_id": "R7"}


async def test_group_question_has_no_code_unless_six_digits_appear():
    body = {**GROUP_TEXT, "msgid": "M2", "text": {"content": "@RobotA fix 1234567 and 12345"}}
    [event] = await delivered_from(msg_frame(body))
    assert event.code is None and event.text == "fix 1234567 and 12345"


async def test_single_chat_uses_the_sender_as_chat_and_a_fixed_thread():
    body = {
        "msgid": "M3",
        "aibotid": BOT,
        "chattype": "single",
        "from": {"userid": "U9"},
        "msgtype": "text",
        "text": {"content": "hello 123456"},
    }
    [event] = await delivered_from(msg_frame(body))
    assert (event.chat_kind, event.external_chat_id, event.external_thread_key) == (
        "dm",
        "U9",
        "dm",
    )
    assert event.code is None and event.text == "hello 123456"


async def test_a_direct_chat_knocks_with_the_code_alone():
    body = {
        "msgid": "M4",
        "aibotid": BOT,
        "chattype": "single",
        "from": {"userid": "U9"},
        "msgtype": "text",
        "text": {"content": " 183237 "},
    }
    [event] = await delivered_from(msg_frame(body))
    assert (event.chat_kind, event.code, event.text) == ("dm", "183237", "183237")


async def test_mixed_messages_join_text_and_flag_attachments_and_images_alone_have_no_text():
    mixed = {
        **GROUP_TEXT,
        "msgid": "M4",
        "msgtype": "mixed",
        "text": None,
        "mixed": {
            "msg_item": [
                {"msgtype": "text", "text": {"content": "@RobotA look"}},
                {"msgtype": "image", "image": {"url": "u"}},
            ]
        },
    }
    image = {
        "msgid": "M5",
        "aibotid": BOT,
        "chattype": "single",
        "from": {"userid": "U9"},
        "msgtype": "image",
        "image": {"url": "u"},
    }
    first, second = await delivered_from(msg_frame(mixed), msg_frame(image))
    assert first.text == "look" and first.has_attachments
    assert second.text == "" and second.has_attachments


async def test_stop_click_on_a_template_card_is_a_stop_event():
    click = {
        "msgid": "EV1",
        "create_time": 1700000000,
        "aibotid": BOT,
        "chatid": "CHAT1",
        "chattype": "group",
        "from": {"userid": "USER1"},
        "msgtype": "event",
        "event": {
            "eventtype": "template_card_event",
            "template_card_event": {
                "card_type": "button_interaction",
                "event_key": "stop:reply-3",
                "task_id": "t",
            },
        },
    }
    other = {**click, "event": {"eventtype": "enter_chat"}}
    wrong_key = {
        **click,
        "event": {"eventtype": "template_card_event", "template_card_event": {"event_key": "ok"}},
    }
    [event] = await delivered_from(
        msg_frame(click, "E9", "aibot_event_callback"),
        msg_frame(other, "E10", "aibot_event_callback"),
        msg_frame(wrong_key, "E11", "aibot_event_callback"),
    )
    assert (event.kind, event.via, event.action_value) == ("stop", "action", "reply-3")
    assert event.message_key == "USER1:1700000000:template_card_event"
    assert event.handle == {"req_id": "E9"} and event.external_chat_id == "CHAT1"


async def test_unsupported_or_malformed_frames_are_ignored():
    voice_in_group = {**GROUP_TEXT, "msgid": "M6", "msgtype": "location"}
    assert await delivered_from(msg_frame(voice_in_group), {"junk": True}) == []


# -- sending ------------------------------------------------------------------


async def live_harness(**kw):
    sock = FakeSocket(**kw)
    h = Harness(sock)
    h.start()
    await h.live()
    return h, sock


def respond_frames(sock: FakeSocket) -> list[dict]:
    return [f for f in sock.sent if f["cmd"] == "aibot_respond_msg"]


async def test_a_reply_is_a_stream_refreshed_with_the_whole_text_and_finished_at_the_end():
    h, sock = await live_harness()
    sent = await h.platform.send(target(), Message("Working", stop_reply_id="r1"))
    sent = await h.platform.edit(target(), sent, Message("Working on it", stop_reply_id="r1"))
    sent = await h.platform.edit(target(), sent, Message("All done", final=True))
    first, second, last = respond_frames(sock)
    assert {f["headers"]["req_id"] for f in (first, second, last)} == {"R1"}
    streams = [f["body"]["stream"] for f in (first, second, last)]
    assert len({s["id"] for s in streams}) == 1
    assert [s["content"] for s in streams] == ["Working", "Working on it", "All done"]
    assert [s["finish"] for s in streams] == [False, False, True]
    assert first["body"]["msgtype"] == "stream"
    await h.finish()


async def test_stop_is_a_separate_template_card_message():
    h, sock = await live_harness()
    await h.platform.send(target(), Message("Working", stop_reply_id="r1"))
    [card] = [f for f in sock.sent if f["cmd"] == "aibot_send_msg"]
    assert card["body"]["msgtype"] == "template_card" and card["body"]["chatid"] == "CHAT1"
    assert card["body"]["chat_type"] == 2
    assert card["body"]["template_card"]["button_list"][0]["key"] == "stop:r1"
    assert all("template_card" not in f["body"] for f in respond_frames(sock))
    assert [f["cmd"] for f in sock.sent][-2:] == ["aibot_send_msg", "aibot_respond_msg"]
    await h.finish()


async def test_a_pressed_stop_card_turns_its_button_into_a_label():
    h, sock = await live_harness()
    await h.platform.settle_stop(target(handle={"req_id": "E9"}), "r1", "Stopping…")
    [update] = [f for f in sock.sent if f["cmd"] == "aibot_respond_update_msg"]
    assert update["headers"]["req_id"] == "E9"
    card = update["body"]["template_card"]
    assert update["body"]["response_type"] == "update_template_card"
    assert card["task_id"] == "stop-r1" and card["main_title"]["title"] == "Stopping…"
    assert [b["key"] for b in card["button_list"]] == ["done:r1"]  # not a Stop any more
    await h.finish()


async def test_frames_for_one_req_id_are_serial_and_each_waits_for_its_ack():
    h, sock = await live_harness(ack=not_acking)
    h.platform._ack_timeout = 5
    for session in h.platform._live.values():
        session.ack_timeout = 5
    sent = Sent(
        "S1", {"mode": "stream", "stream_id": "S1", "req_id": "R1", "started_at": h.clock()}
    )
    one = asyncio.ensure_future(h.platform.edit(target(), sent, Message("one")))
    two = asyncio.ensure_future(h.platform.edit(target(), sent, Message("one two")))
    await until(lambda: len(respond_frames(sock)) == 1)
    await asyncio.sleep(0.02)
    assert len(respond_frames(sock)) == 1  # the second waits for the first ACK
    sock.push({"headers": {"req_id": "R1"}, "errcode": 0, "errmsg": "ok"})
    await until(lambda: len(respond_frames(sock)) == 2)
    sock.push({"headers": {"req_id": "R1"}, "errcode": 0, "errmsg": "ok"})
    await asyncio.wait_for(asyncio.gather(one, two), 2)
    assert [f["body"]["stream"]["content"] for f in respond_frames(sock)] == ["one", "one two"]
    await h.finish()


async def test_at_nine_minutes_the_stream_finishes_and_the_answer_is_pushed_later():
    h, sock = await live_harness()
    sent = await h.platform.send(target(), Message("Working"))
    h.clock.advance(541)
    sent = await h.platform.edit(target(), sent, Message("still going"))
    closing = respond_frames(sock)[-1]["body"]["stream"]
    assert closing["finish"] is True and closing["content"] == "Still working…"
    assert sent.ref["mode"] == "pushed"
    before = len(sock.sent)
    sent = await h.platform.edit(target(), sent, Message("still going more"))
    assert len(sock.sent) == before  # nothing more can be refreshed
    sent = await h.platform.edit(
        target(), sent, Message("The answer", final=True, open_url="https://x.test/o")
    )
    push = sock.sent[-1]
    assert push["cmd"] == "aibot_send_msg"
    assert push["body"]["markdown"]["content"].startswith("The answer")
    assert "[Open in PuppyOne](https://x.test/o)" in push["body"]["markdown"]["content"]
    count = len(sock.sent)
    await h.platform.edit(target(), sent, Message("The answer", final=True))
    assert len(sock.sent) == count  # the final answer is pushed once
    await h.finish()


async def test_text_is_cut_to_20480_utf8_bytes_with_the_link_at_the_end():
    h, sock = await live_harness()
    await h.platform.send(target(), Message("好" * 10_000, final=True, open_url="https://x.test/o"))
    content = respond_frames(sock)[0]["body"]["stream"]["content"]
    assert len(content.encode()) <= 20480
    assert content.endswith("[Open in PuppyOne](https://x.test/o)")
    await h.finish()


async def test_notify_is_a_plain_push_to_the_chat():
    h, sock = await live_harness()
    await h.platform.notify(target(), "Only the asker can stop this.")
    push = sock.sent[-1]
    assert push["cmd"] == "aibot_send_msg" and push["body"]["markdown"]["content"].startswith(
        "Only"
    )
    await h.finish()


async def test_sending_without_holding_the_bots_socket_raises_not_here():
    platform = WeComPlatform()
    with pytest.raises(NotHere):
        await platform.send(target(), Message("x"))
    with pytest.raises(NotHere):
        await platform.edit(
            target(),
            Sent("s", {"mode": "stream", "req_id": "R", "stream_id": "s", "started_at": 0}),
            Message("x"),
        )
    with pytest.raises(NotHere):
        await platform.notify(target(), "x")
    h, _ = await live_harness()
    with pytest.raises(NotHere):
        await h.platform.send(target(account_id="another-bot"), Message("x"))
    await h.finish()


async def test_a_bot_may_send_thirty_frames_a_minute():
    h, sock = await live_harness()
    sent = await h.platform.send(target(), Message("start"))
    for i in range(29):
        await h.platform.edit(target(), sent, Message(f"t{i}"))
    with pytest.raises(RateLimited) as info:
        await h.platform.edit(target(), sent, Message("one too many"))
    assert 0 < info.value.retry_after <= 2.1
    h.clock.advance(2.1)
    await h.platform.edit(target(), sent, Message("again"))
    assert json.loads(json.dumps(sock.sent[-1]))["cmd"] == "aibot_respond_msg"
    await h.finish()
