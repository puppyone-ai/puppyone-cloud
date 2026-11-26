from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from src.platform.chat_apps.platforms import build_platforms
from src.platform.chat_apps.platforms.slack import SlackPlatform
from src.platform.chat_apps.ports import (
    Message,
    MessageGone,
    NotVerified,
    PlatformError,
    RateLimited,
    Sent,
    Target,
)

from .platform_support import Calls, FakeClock

SIGNING = "8f742231b10e8888abcd99yyyzzz85a5"
NOW = 1_531_420_618.0
TEAM, BOT = "T111", "UBOT"


def make(calls: Calls | None = None, clock: FakeClock | None = None) -> SlackPlatform:
    return SlackPlatform(
        "cid",
        "csecret",
        SIGNING,
        "https://api.puppyone.test",
        "https://slack.test/api",
        "https://slack.test/oauth/v2/authorize",
        transport=(calls or Calls()).transport,
        clock=clock or FakeClock(NOW),
    )


def sign(body: bytes, stamp: str = str(int(NOW)), secret: str = SIGNING) -> dict[str, str]:
    digest = hmac.new(secret.encode(), b"v0:" + stamp.encode() + b":" + body, hashlib.sha256)
    return {
        "X-Slack-Request-Timestamp": stamp,
        "X-Slack-Signature": "v0=" + digest.hexdigest(),
    }


def envelope(event: dict, **extra) -> bytes:
    return json.dumps(
        {
            "token": "x",
            "team_id": TEAM,
            "api_app_id": "A1",
            "type": "event_callback",
            "event_id": "Ev123",
            "event_time": 1700000000,
            "authorizations": [{"team_id": TEAM, "user_id": BOT, "is_bot": True}],
            "event": event,
            **extra,
        }
    ).encode()


def parse(event: dict, **extra):
    return make().parse({}, envelope(event, **extra))


# -- verify / handshake -----------------------------------------------------


def test_verify_accepts_a_signed_request():
    body = b'{"type":"event_callback"}'
    make().verify(sign(body), body)
    make().verify({k.lower(): v for k, v in sign(body).items()}, body)


def test_verify_rejects_a_flipped_body_byte_and_a_flipped_signature_byte():
    body = b'{"type":"event_callback"}'
    headers = sign(body)
    with pytest.raises(NotVerified):
        make().verify(headers, b'{"type":"event_callbacK"}')
    headers["X-Slack-Signature"] = headers["X-Slack-Signature"][:-1] + (
        "0" if headers["X-Slack-Signature"][-1] != "0" else "1"
    )
    with pytest.raises(NotVerified):
        make().verify(headers, body)


def test_verify_rejects_a_wrong_secret_and_missing_headers():
    body = b"{}"
    with pytest.raises(NotVerified):
        make().verify(sign(body, secret="other"), body)
    with pytest.raises(NotVerified):
        make().verify({}, body)


def test_verify_rejects_a_timestamp_older_than_five_minutes_but_not_four():
    body = b"{}"
    stale = str(int(NOW) - 301)
    with pytest.raises(NotVerified):
        make().verify(sign(body, stale), body)
    make().verify(sign(body, str(int(NOW) - 240)), body)
    with pytest.raises(NotVerified):
        make().verify(sign(body, str(int(NOW) + 301)), body)


def test_url_verification_handshake_returns_the_challenge():
    body = json.dumps(
        {
            "type": "url_verification",
            "challenge": "3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P",
            "token": "t",
        }
    ).encode()
    assert make().handshake({}, body) == {
        "challenge": "3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P"
    }
    assert make().handshake({}, envelope({"type": "app_mention"})) is None
    assert make().handshake({}, b"junk") is None


# -- parse ------------------------------------------------------------------


MENTION = {
    "type": "app_mention",
    "user": "U1",
    "text": f"<@{BOT}> write the notes",
    "ts": "1700000000.000100",
    "channel": "C1",
    "event_ts": "1700000000.000100",
}


def test_app_mention_is_a_question_with_the_mention_removed():
    [event] = parse(MENTION)
    assert (event.kind, event.via, event.text) == ("question", "message", "write the notes")
    assert event.message_key == f"{TEAM}:C1:1700000000.000100"
    assert event.external_thread_key == "1700000000.000100"
    assert event.account_id == TEAM
    assert event.asker_external_id == "U1"
    assert event.asker_trust == "member"
    assert event.chat_kind == "group"
    assert event.external_event_id == "Ev123"


def test_a_mention_inside_a_thread_keeps_the_thread_key():
    [event] = parse({**MENTION, "thread_ts": "1699999999.000001"})
    assert event.external_thread_key == "1699999999.000001"
    assert event.message_id == "1700000000.000100"


def test_direct_message_is_a_question_in_the_fixed_dm_thread():
    dm = {
        "type": "message",
        "channel_type": "im",
        "channel": "D1",
        "user": "U1",
        "text": " hello there ",
        "ts": "1700000001.000200",
    }
    [event] = parse(dm)
    assert (event.chat_kind, event.external_thread_key, event.text) == ("dm", "dm", "hello there")
    assert event.message_key == f"{TEAM}:D1:1700000001.000200"


@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "message",
            "subtype": "message_changed",
            "channel_type": "im",
            "channel": "D1",
            "ts": "1.1",
        },
        {
            "type": "message",
            "subtype": "message_deleted",
            "channel_type": "im",
            "channel": "D1",
            "ts": "1.1",
        },
        {
            "type": "message",
            "subtype": "bot_message",
            "channel_type": "im",
            "channel": "D1",
            "ts": "1.1",
            "bot_id": "B1",
        },
        {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "ts": "1.1",
            "bot_id": "B1",
            "text": "x",
        },
        {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": BOT,
            "ts": "1.1",
            "text": "echo",
        },
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C1",
            "user": "U1",
            "ts": "1.1",
            "text": "not for us",
        },
        {**MENTION, "bot_id": "B9"},
        {**MENTION, "subtype": "message_changed"},
        {"type": "link_shared", "channel": "C1"},
        {"type": "reaction_added", "user": "U1"},
    ],
)
def test_events_not_addressed_to_the_agent_produce_nothing(event):
    assert parse(event) == []


def test_slack_connect_and_guest_askers_are_marked_but_still_returned():
    [external] = parse({**MENTION, "user_team": "T999"})
    assert external.asker_trust == "external"
    [guest] = parse({**MENTION, "user_profile": {"is_restricted": True}})
    assert guest.asker_trust == "guest"
    [member] = parse({**MENTION, "user_team": TEAM})
    assert member.asker_trust == "member"


def test_bot_joining_a_channel_is_a_joined_event_with_the_inviter():
    joined = {
        "type": "member_joined_channel",
        "user": BOT,
        "channel": "C2",
        "channel_type": "C",
        "team": TEAM,
        "inviter": "U1",
    }
    [event] = parse(joined)
    assert (event.kind, event.external_chat_id, event.asker_external_id) == ("joined", "C2", "U1")
    assert event.account_id == TEAM and event.message_key
    [no_inviter] = parse({k: v for k, v in joined.items() if k != "inviter"})
    assert no_inviter.asker_external_id is None
    assert parse({**joined, "user": "U7"}) == []


@pytest.mark.parametrize("kind", ["app_uninstalled", "tokens_revoked"])
def test_uninstall_and_revocation_are_uninstalled_events(kind):
    [event] = parse({"type": kind})
    assert (event.kind, event.account_id, event.external_chat_id) == ("uninstalled", TEAM, TEAM)
    assert "Ev123" in event.message_key


def interactivity(action_id="stop", value="reply-9") -> bytes:
    payload = {
        "type": "block_actions",
        "team": {"id": TEAM, "domain": "d"},
        "user": {"id": "U2", "username": "sam", "team_id": TEAM},
        "api_app_id": "A1",
        "trigger_id": "13345224609.738474920.8088930838d88f008e0",
        "channel": {"id": "C1", "name": "launch"},
        "message": {"ts": "1700000005.000300", "thread_ts": "1700000000.000100"},
        "response_url": "https://hooks.slack.com/actions/T111/1/abc",
        "actions": [
            {
                "action_id": action_id,
                "block_id": "b",
                "value": value,
                "type": "button",
                "action_ts": "1700000006.1",
            }
        ],
    }
    return urlencode({"payload": json.dumps(payload)}).encode()


def test_stop_button_click_is_a_stop_event_with_the_response_url():
    [event] = make().parse({}, interactivity())
    assert (event.kind, event.via, event.action_value) == ("stop", "action", "reply-9")
    assert event.message_key == "act:13345224609.738474920.8088930838d88f008e0"
    assert event.handle == {"response_url": "https://hooks.slack.com/actions/T111/1/abc"}
    assert event.asker_external_id == "U2" and event.account_id == TEAM
    assert event.external_chat_id == "C1"


def test_other_buttons_are_ignored():
    assert make().parse({}, interactivity(action_id="open")) == []
    assert make().parse({}, b"payload=%7Bbroken") == []
    assert make().parse({}, b"junk") == []


# -- OAuth ------------------------------------------------------------------


def test_authorize_url_carries_scopes_redirect_and_state():
    url = urlparse(make().authorize_url("STATE.1"))
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://slack.test/oauth/v2/authorize"
    assert query["client_id"] == ["cid"]
    assert query["scope"] == ["app_mentions:read,chat:write,im:history,channels:read,groups:read"]
    assert query["redirect_uri"] == [
        "https://api.puppyone.test/api/v1/chat-apps/slack/oauth/callback"
    ]
    assert query["state"] == ["STATE.1"]


async def test_exchange_returns_the_team_the_token_and_the_installer():
    calls = Calls(
        lambda method, body: {
            "ok": True,
            "access_token": "xoxb-1",
            "bot_user_id": BOT,
            "team": {"id": TEAM, "name": "Acme"},
            "authed_user": {"id": "U1"},
        }
    )
    result = await make(calls).exchange("the-code")
    assert result["team_id"] == TEAM and result["team_name"] == "Acme"
    assert result["bot_token"] == "xoxb-1" and result["installer_user_id"] == "U1"
    [(method, body)] = calls.requests
    assert method == "oauth.v2.access"
    assert body["code"] == "the-code" and body["client_secret"] == "csecret"


async def test_exchange_failure_raises():
    calls = Calls(lambda method, body: {"ok": False, "error": "invalid_code"})
    with pytest.raises(PlatformError):
        await make(calls).exchange("bad")


# -- pacing and sending ------------------------------------------------------


def target(**extra) -> Target:
    return Target(
        "slack",
        "C1",
        account_id=TEAM,
        credential="xoxb-1",
        external_thread_key="1700000000.000100",
        reply_to="1700000000.000100",
        asker_external_id="U1",
        **extra,
    )


def test_pacing_values():
    pacing = make().pacing(target())
    assert (pacing.stream_seconds, pacing.stream_chars, pacing.bytes_limit) == (540, 11000, False)
    assert pacing.min_edit_seconds == 1.0


def stream_reply(method, body):
    if method == "chat.postMessage":
        return {"ok": True, "ts": "9.9"}
    return {"ok": True, "ts": "5.5", "channel": "C1"}


async def test_progress_streams_in_the_thread_and_appends_only_the_new_suffix():
    calls = Calls(stream_reply)
    platform = make(calls)
    sent = await platform.send(target(), Message("Hello", stop_reply_id="r1"))
    start = calls.of("chat.startStream")[0]
    assert start["thread_ts"] == "1700000000.000100"
    assert (start["recipient_team_id"], start["recipient_user_id"]) == (TEAM, "U1")
    assert start["markdown_text"] == "Hello"
    assert calls.headers[0]["authorization"] == "Bearer xoxb-1"
    sent = await platform.edit(target(), sent, Message("Hello world", stop_reply_id="r1"))
    sent = await platform.edit(target(), sent, Message("Hello world, done", stop_reply_id="r1"))
    assert [b["markdown_text"] for b in calls.of("chat.appendStream")] == [" world", ", done"]
    assert sent.message_id == "5.5"
    # the Stop button is a companion message with action_id "stop"
    [companion] = calls.of("chat.postMessage")
    button = companion["blocks"][0]["elements"][0]
    assert (button["action_id"], button["value"]) == ("stop", "r1")
    assert companion["thread_ts"] == "1700000000.000100"


async def test_final_always_stops_the_stream_and_removes_the_stop_button():
    calls = Calls(stream_reply)
    platform = make(calls)
    sent = await platform.send(target(), Message("Hi", stop_reply_id="r1"))
    sent = await platform.edit(
        target(), sent, Message("Hi there", final=True, open_url="https://x.test/o")
    )
    assert calls.methods()[-2:] == ["chat.stopStream", "chat.delete"]
    stop = calls.of("chat.stopStream")[0]
    assert stop["ts"] == "5.5"
    assert "Open in PuppyOne" in json.dumps(stop["blocks"])
    assert calls.of("chat.appendStream")[0]["markdown_text"] == " there"


async def test_stream_rolls_over_at_nine_minutes_and_continues_in_a_new_message():
    clock = FakeClock(NOW)
    ts = iter(["5.5", "6.6"])

    def reply(method, body):
        if method == "chat.startStream":
            return {"ok": True, "ts": next(ts), "channel": "C1"}
        return {"ok": True, "ts": "9.9"}

    calls = Calls(reply)
    platform = make(calls, clock)
    sent = await platform.send(target(), Message("one", stop_reply_id="r1"))
    clock.advance(541)
    sent = await platform.edit(target(), sent, Message("one two", stop_reply_id="r1"))
    assert calls.of("chat.appendStream")[0]["markdown_text"] == " two"
    assert calls.of("chat.stopStream")[0]["ts"] == "5.5"
    assert sent.ref["ts"] is None  # nothing left to say yet; the next text opens a new stream
    sent = await platform.edit(target(), sent, Message("one two three", stop_reply_id="r1"))
    second = calls.of("chat.startStream")[1]
    assert second["markdown_text"] == " three"
    assert sent.message_id == "6.6"
    sent = await platform.edit(target(), sent, Message("one two three!", final=True))
    assert calls.of("chat.stopStream")[-1]["ts"] == "6.6"


async def test_stream_rolls_over_at_11000_characters():
    ts = iter(["5.5", "6.6"])

    def reply(method, body):
        if method == "chat.startStream":
            return {"ok": True, "ts": next(ts)}
        return {"ok": True, "ts": "9.9"}

    calls = Calls(reply)
    platform = make(calls)
    sent = await platform.send(target(), Message("a" * 10_000, stop_reply_id="r1"))
    sent = await platform.edit(
        target(), sent, Message("a" * 10_000 + "b" * 2_500, stop_reply_id="r1")
    )
    assert len(calls.of("chat.appendStream")[0]["markdown_text"]) == 1_000
    assert calls.of("chat.stopStream")[0]["ts"] == "5.5"
    assert calls.of("chat.startStream")[1]["markdown_text"] == "b" * 1_500
    assert sent.message_id == "6.6"


async def test_status_lines_use_post_message_and_chat_update():
    calls = Calls()
    platform = make(calls)
    sent = await platform.send(target(), Message("Queued behind another question."))
    [post] = calls.of("chat.postMessage")
    assert post["thread_ts"] == "1700000000.000100"
    sent = await platform.edit(target(), Sent("9.9", {"mode": "message"}), Message("Starting now."))
    [update] = calls.of("chat.update")
    assert update["ts"] == "9.9" and update["text"] == "Starting now."
    assert sent.message_id == "9.9"


async def test_dm_thread_is_the_question_not_the_dm_key():
    calls = Calls()
    dm = Target(
        "slack",
        "D1",
        chat_kind="dm",
        account_id=TEAM,
        credential="t",
        external_thread_key="dm",
        reply_to="1.5",
    )
    await make(calls).send(dm, Message("hi"))
    assert calls.of("chat.postMessage")[0]["thread_ts"] == "1.5"


async def test_notify_is_ephemeral_to_the_asker():
    calls = Calls()
    await make(calls).notify(target(), "Only the asker can stop this.")
    [body] = calls.of("chat.postEphemeral")
    assert (body["user"], body["channel"], body["thread_ts"]) == ("U1", "C1", "1700000000.000100")


async def test_429_and_ratelimited_become_rate_limited():
    calls = Calls(lambda m, b: (429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "12"}))
    with pytest.raises(RateLimited) as info:
        await make(calls).send(target(), Message("x"))
    assert info.value.retry_after == 12
    calls = Calls(lambda m, b: (200, {"ok": False, "error": "ratelimited"}, {"Retry-After": "3"}))
    with pytest.raises(RateLimited) as info:
        await make(calls).send(target(), Message("x"))
    assert info.value.retry_after == 3


async def test_message_not_found_is_message_gone():
    calls = Calls(lambda m, b: {"ok": False, "error": "message_not_found"})
    with pytest.raises(MessageGone):
        await make(calls).edit(target(), Sent("1.1", {"mode": "message"}), Message("x"))


async def test_other_errors_are_platform_errors_and_never_leak_the_token():
    calls = Calls(lambda m, b: {"ok": False, "error": "token_revoked"})
    with pytest.raises(PlatformError) as info:
        await make(calls).send(target(), Message("x"))
    assert "xoxb" not in str(info.value)


async def test_start_stream_bucket_is_per_workspace_about_twenty_a_minute():
    clock = FakeClock(NOW)
    calls = Calls(stream_reply)
    platform = make(calls, clock)
    for _ in range(20):
        await platform.send(target(), Message("x", stop_reply_id="r"))
    with pytest.raises(RateLimited) as info:
        await platform.send(target(), Message("x", stop_reply_id="r"))
    assert 0 < info.value.retry_after <= 3.1
    other = Target("slack", "C1", account_id="T2", credential="xoxb-2", reply_to="1.1")
    await platform.send(other, Message("x", stop_reply_id="r"))
    clock.advance(3.1)
    await platform.send(target(), Message("x", stop_reply_id="r"))


async def test_chat_update_bucket_allows_fifty_a_minute():
    platform = make(Calls(), FakeClock(NOW))
    sent = Sent("1.1", {"mode": "message"})
    for _ in range(50):
        await platform.edit(target(), sent, Message("x"))
    with pytest.raises(RateLimited):
        await platform.edit(target(), sent, Message("x"))


def test_build_platforms_wires_the_slack_settings():
    class Settings:
        chat_app_platforms = frozenset({"slack"})
        CHAT_APPS_SLACK_CLIENT_ID = "cid"
        CHAT_APPS_SLACK_CLIENT_SECRET = "cs"
        CHAT_APPS_SLACK_SIGNING_SECRET = SIGNING
        CHAT_APPS_SLACK_API_BASE = "https://slack.test/api"
        CHAT_APPS_SLACK_AUTHORIZE_URL = "https://slack.test/authorize"
        PUBLIC_URL = "https://api.puppyone.test"

    built = build_platforms(Settings())
    assert list(built) == ["slack"]
    assert (
        "redirect_uri=https%3A%2F%2Fapi.puppyone.test%2Fapi%2Fv1%2Fchat-apps%2Fslack%2Foauth%2Fcallback"
        in built["slack"].authorize_url("s")
    )


async def test_a_knocking_channel_is_named_by_asking_slack():
    calls = Calls(lambda method, body: {"ok": True, "channel": {"id": "C1", "name": "launch"}})
    platform = make(calls)
    assert await platform.chat_title(target(chat_kind="group")) == "#launch"
    [(method, body)] = calls.requests
    assert (method, body) == ("conversations.info", {"channel": "C1"})
    assert await platform.chat_title(target(chat_kind="dm")) is None
    refused = make(Calls(lambda method, body: {"ok": False, "error": "channel_not_found"}))
    assert await refused.chat_title(target(chat_kind="group")) is None
