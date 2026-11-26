"""Relay: a Run as it is now -> what its one chat message says, per state and pace."""

from datetime import UTC, datetime, timedelta

import pytest

from src.platform.chat_apps.ports import Message, Pacing
from src.platform.chat_apps.relay import (
    changed_files,
    digest,
    due,
    notice_text,
    render,
    stop_refusal,
    truncate,
    welcome_code,
)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
TELEGRAM = Pacing(min_edit_seconds=3.0, max_text=4096)


def run(state, *, text="", tools=(), code=None, seconds=20, publication=None):
    return {
        "id": "r1",
        "state": state,
        "session_id": "s1",
        "snapshot": {"text": text, **({"code": code} if code else {})},
        "tools": list(tools),
        "publication": publication,
        "created_at": NOW.isoformat(),
        "updated_at": (NOW + timedelta(seconds=seconds)).isoformat(),
    }


def tool(name, state="succeeded", **data):
    return {"name": name, "state": state, "input": data}


def show(value, pacing=TELEGRAM):
    return render(
        value,
        reply_id="rep",
        owner="Alex",
        asker="Mia",
        open_url="https://web/open/projects/p?session=s1",
        pacing=pacing,
        now=NOW,
    )


def test_progress_says_only_the_current_step_with_stop():
    assert show(run("queued")).message.text == "Starting up…"
    reading = show(run("running", tools=[tool("read", path="/workspace/repo/pricing.md")]))
    assert reading.message.text == "Reading pricing.md…"
    assert reading.message.stop_reply_id == "rep" and not reading.finished
    assert show(run("running", tools=[tool("edit", path="a/b.md")])).message.text == "Editing b.md…"
    assert (
        show(run("running", tools=[tool("bash", command="ls")])).message.text
        == "Running a command…"
    )
    assert show(run("running")).message.text == "Working…"
    assert show(run("running", text="The price is")).message.text == "The price is"


def test_waiting_for_approval_names_the_owner_and_the_action_without_buttons():
    waiting = show(
        run("waiting_approval", tools=[tool("bash", "waiting", command="git reset --hard")])
    )
    assert waiting.message.text == "Waiting for Alex to approve in PuppyOne: bash git reset --hard"
    assert waiting.message.button is None
    denied = show(run("running", tools=[tool("bash", "rejected", command="rm -r x")]))
    assert denied.message.text == "Denied by Alex."


def test_finished_runs():
    done = show(
        run(
            "succeeded",
            text="Done, it is 29 now.",
            tools=[tool("edit", path="/workspace/repo/pricing.md")],
            seconds=30,
        )
    )
    assert done.finished and done.message.final and done.units == 1 and not done.done_notice
    assert done.message.text == "Done, it is 29 now.\n\nChanged 1 file: pricing.md"
    assert done.message.open_url.endswith("session=s1") and done.message.stop_reply_id is None
    long = show(run("succeeded", text="ok", seconds=61))
    assert long.done_notice and long.units == 2
    assert (
        show(run("stopped", code="approval_expired")).message.text
        == "No answer in 30 minutes. Stopped."
    )
    stopped = show(run("stopped", tools=[tool("write", path="notes.md")]))
    assert stopped.message.text == "Stopped. Changed so far: notes.md"
    assert show(run("outcome_unknown")).message.text.startswith("I lost track of this one.")
    assert show(run("failed")).message.text == notice_text("failed")
    partway = show(run("failed", tools=[tool("edit", path="a.md")]))
    assert partway.message.text == "Something went wrong partway. Changed so far: a.md"
    hours = show(run("failed", code="runtime_credit_unavailable"))
    assert (
        hours.message.text == "This space has no Agent hours left. Alex can add more in PuppyOne."
    )


def test_changed_files_prefer_the_publication_then_tool_paths():
    assert changed_files(run("succeeded", publication={"paths": ["b", "a"]})) == ["a", "b"]
    tools = [
        tool("edit", path="/workspace/repo/x.md"),
        tool("write", path="/workspace/repo/x.md"),
        tool("write", "rejected", path="y.md"),
        tool("read", path="z.md"),
    ]
    assert changed_files(run("succeeded", tools=tools)) == ["x.md"]


@pytest.mark.parametrize(
    ("pacing", "text", "limit"),
    [
        (Pacing(3.0, 4096), "x" * 5000, 4096),
        (Pacing(2.0, 20480, bytes_limit=True), "汉" * 9000, 20480),
    ],
)
def test_long_answers_are_cut_not_split(pacing, text, limit):
    cut = truncate(text, pacing, "Open in PuppyOne")
    size = len(cut.encode()) if pacing.bytes_limit else len(cut)
    assert size <= limit and cut.endswith("…")


def test_edits_wait_for_the_platform_pace_except_the_final():
    assert due(None, TELEGRAM, NOW, False)
    assert not due(NOW - timedelta(seconds=1), TELEGRAM, NOW, False)
    assert due(NOW - timedelta(seconds=3), TELEGRAM, NOW, False)
    assert due(NOW, TELEGRAM, NOW, True)


def test_unchanged_content_has_the_same_digest():
    assert digest(Message("a")) == digest(Message("a"))
    assert digest(Message("a")) != digest(Message("a", final=True))


def test_the_hello_says_how_to_ask_there():
    assert welcome_code("telegram", "group") == "welcome_telegram"
    assert welcome_code("slack", "group") == "welcome"
    assert welcome_code("telegram", "dm") == welcome_code("slack", "dm") == "welcome_dm"
    assert notice_text("welcome_dm") == "I'm in. Send me a message to ask."


def test_stop_refusal_names_each_person_once():
    assert stop_refusal("Mia", "Alex") == "Only Mia or Alex can stop this."
    assert stop_refusal("Alex", "Alex") == "Only Alex can stop this."


def test_notice_copy():
    assert notice_text("only_owner", name="Alex") == "Only Alex can ask me here."
    assert notice_text("welcome", mention="@PuppyOne", name="Alex").startswith(
        "I'm in. Mention @PuppyOne to ask."
    )
