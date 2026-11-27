"""A Run as it is now -> what its one chat message says. Pure; no I/O.

Copy is the English original of 09-chat-apps.md "What the Agent says".
"""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.platform.chat_apps.ports import Message, Pacing

ACTIVE = frozenset({"queued", "running", "waiting_approval", "publishing"})
DONE_NOTICE_AFTER = timedelta(seconds=60)
WORKSPACE_PREFIX = "/workspace/repo/"
FAILED_GIVE_UP = 5
OPEN = "Open in PuppyOne"

NOTICES = {
    "welcome": "I'm in. Mention {mention} to ask. I only read messages that mention me. "
    "Only {name} can ask for now.",
    # Telegram keeps privacy mode on: a plain @mention does not reach a bot that
    # is in the group, so only promise what arrives (replies and /ask@bot).
    "welcome_telegram": "I'm in. Reply to me or send /ask{mention} to ask. "
    "I only read messages meant for me. Only {name} can ask for now.",
    "welcome_dm": "I'm in. Send me a message to ask.",
    "new_thread": "New conversation. Ask away.",
    "only_owner": "Only {name} can ask me here.",
    "outsider": "Only people in {workspace} can ask me.",
    "link_account": "Link your PuppyOne account to ask.",
    "not_in_project": "You're not in this project. Ask {name} to invite you.",
    "not_your_stop": "Only {asker} or {name} can stop this.",
    "not_your_stop_one": "Only {name} can stop this.",
    "stopping": "Stopping…",
    "finished": "Already finished.",
    "queued": "I'll get to this next.",
    "too_many": "Too many waiting. Ask again in a bit.",
    "limit": "Today's limit here is used up. I'll answer again tomorrow.",
    "no_hours": "This space has no Agent hours left. {name} can add more in PuppyOne.",
    "failed": "Something went wrong on our side. Nothing was changed. Ask again.",
    "paused": "I'm paused here. {name} can turn me back on in PuppyOne.",
    "removed": "I've been removed from this chat.",
    "attachments": "I can't read attachments here yet. Paste the text, or put the file in the project.",
    "not_connected": "This chat isn't connected to a project yet. "
    "Open PuppyOne → Agents → Add to chat.",
    "stale": "Something went wrong on our side. Nothing was changed. Ask again.",
    "unconfigured": "The agent isn't set up for this project yet. {name} can set it up in PuppyOne.",
}


def notice_text(code: str, **values: str) -> str:
    return NOTICES[code].format_map({key: value or "" for key, value in values.items()})


def welcome_code(platform: str, chat_kind: str) -> str:
    """The hello that says how to ask: every message in a direct chat; in a
    group a mention, and on Telegram a reply too."""
    if chat_kind == "dm":
        return "welcome_dm"
    return "welcome_telegram" if platform == "telegram" else "welcome"


def stop_refusal(asker: str, owner: str) -> str:
    """Who may press Stop, naming one person once when the asker owns the chat."""
    if asker == owner:
        return notice_text("not_your_stop_one", name=owner)
    return notice_text("not_your_stop", asker=asker, name=owner)


def changed_files(run: dict) -> list[str]:
    """Files the Run changed: the publication's list, else write/edit tool paths."""
    published = (run.get("publication") or {}).get("paths")
    if isinstance(published, list):
        return sorted({str(path) for path in published})
    paths = []
    for tool in run.get("tools") or []:
        if tool.get("name") in ("write", "edit") and tool.get("state") not in (
            "rejected",
            "failed",
        ):
            path = str((tool.get("input") or {}).get("path") or "")
            if path.startswith(WORKSPACE_PREFIX):
                path = path[len(WORKSPACE_PREFIX) :]
            if path:
                paths.append(path.lstrip("/"))
    return sorted(set(paths))


def _changed_line(files: list[str], prefix: str) -> str:
    return f"{prefix}: " + ", ".join(files) if files else ""


def _what(tool: dict) -> str:
    data = tool.get("input") or {}
    target = data.get("command") or data.get("path") or ""
    return f"{tool.get('name')} {target}".strip()[:200]


def _step(tools: list[dict]) -> str:
    for tool in reversed(tools):
        name = tool.get("name")
        path = posixpath.basename(str((tool.get("input") or {}).get("path") or "")) or "a file"
        if name == "read":
            return f"Reading {path}…"
        if name in ("write", "edit"):
            return f"Editing {path}…"
        if name == "bash":
            return "Running a command…"
    return "Working…"


@dataclass(frozen=True)
class Render:
    message: Message
    finished: bool
    #: Runtime minutes this Run used (counted against the chat's daily limit).
    units: int = 0
    #: Say "Done · {asker}" in a new message (edits notify nobody).
    done_notice: bool = False


def truncate(text: str, pacing: Pacing, tail: str) -> str:
    budget = pacing.max_text - len(tail) - 2
    if pacing.bytes_limit:
        raw = text.encode()
        if len(raw) + len(tail.encode()) + 2 <= pacing.max_text:
            return text
        return raw[: max(0, budget - len(tail.encode()) + len(tail))].decode(errors="ignore") + "…"
    return text if len(text) <= budget else text[: max(0, budget - 1)] + "…"


def render(
    run: dict,
    *,
    reply_id: str,
    owner: str,
    asker: str,
    open_url: str,
    pacing: Pacing,
    now: datetime,
) -> Render:
    state = run["state"]
    snapshot = run.get("snapshot") or {}
    tools = run.get("tools") or []
    text = (snapshot.get("text") or "").strip()
    files = changed_files(run)
    if state in ACTIVE:
        if state == "waiting_approval":
            waiting = next((t for t in reversed(tools) if t.get("state") == "waiting"), {})
            body = f"Waiting for {owner} to approve in PuppyOne: {_what(waiting)}"
        elif state == "queued":
            body = "Starting up…"
        else:
            last = tools[-1] if tools else {}
            if last.get("state") == "rejected":
                body = f"Denied by {owner}."
            else:
                body = text or _step(tools)
        status = body != text
        return Render(
            Message(truncate(body, pacing, OPEN), stop_reply_id=reply_id, status=status), False
        )

    code = snapshot.get("code")
    if state == "succeeded":
        line = _changed_line(files, f"Changed {len(files)} file{'' if len(files) == 1 else 's'}")
        body = "\n\n".join(part for part in (text or "Done.", line) if part)
    elif state == "stopped" and code == "approval_expired":
        body = "No answer in 30 minutes. Stopped."
    elif state == "stopped":
        body = "Stopped." + (" " + _changed_line(files, "Changed so far") if files else "")
    elif state == "outcome_unknown":
        body = "I lost track of this one. Check the files in PuppyOne before asking again."
    elif code == "runtime_credit_unavailable":
        body = notice_text("no_hours", name=owner)
    elif files:
        body = "Something went wrong partway. " + _changed_line(files, "Changed so far")
    else:
        body = notice_text("failed")
    started = datetime.fromisoformat(run["created_at"])
    ended = datetime.fromisoformat(run.get("updated_at") or run["created_at"])
    seconds = max(0.0, (ended - started).total_seconds())
    return Render(
        Message(truncate(body, pacing, OPEN), open_url=open_url, final=True),
        True,
        units=max(1, -(-int(seconds) // 60)),
        done_notice=(ended - started) > DONE_NOTICE_AFTER,
    )


def digest(message: Message) -> str:
    raw = "\x1f".join(
        str(part) for part in (message.text, message.stop_reply_id, message.open_url, message.final)
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def due(last_edit: datetime | None, pacing: Pacing, now: datetime, final: bool) -> bool:
    """Edit now? Final messages go out at once; progress waits for the platform's pace."""
    if final or last_edit is None:
        return True
    return (now - last_edit).total_seconds() >= pacing.min_edit_seconds
