"""Run with python -m src.platform.chat_apps.gateway (SERVICE_ROLE=chat_gateway).

Three loops, written like the agent worker: the database decides, this process
only holds leases.

- inbox: claim one stored event, decide (``dispatch``), act, finish it;
- replies: claim one chat message that follows a Run (or a one-line notice),
  read the Run, edit the message at the platform's pace (``relay``);
- WeCom: hold the long connection of each bot whose lease we own.

On SIGTERM it stops claiming and returns its leases; a successor continues from
the database and edits the same messages. Blocking calls go through run_blocking.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from src.infra.execution.runner import run_blocking
from src.platform.agent.runtime.control.models import SubmitRun
from src.platform.chat_apps import codes, credentials
from src.platform.chat_apps.dispatch import decide, thread_key
from src.platform.chat_apps.ports import (
    Inbound,
    Message,
    MessageGone,
    NotHere,
    PlatformError,
    RateLimited,
    Sent,
    Target,
)
from src.platform.chat_apps.relay import (
    FAILED_GIVE_UP,
    digest,
    due,
    notice_text,
    render,
)
from src.platform.chat_apps.repository import ChatAppRepository, StaleFence

logger = logging.getLogger(__name__)
LEASE_SECONDS = 30
STATS_INTERVAL_SECONDS = 60
SCRUB_INTERVAL_SECONDS = 3600
IDLE_SECONDS = 1.0
MAX_INBOX_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Gateway:
    def __init__(
        self,
        repository,
        agents,
        platforms,
        entitlements=None,
        *,
        worker=None,
        web_origin="",
        clock=_now,
    ):
        self.repository = repository
        self.agents = agents
        self.platforms = platforms
        self.entitlements = entitlements
        self.worker = worker or f"chat-gateway-{socket.gethostname()}-{os.getpid()}"
        self.web_origin = web_origin.rstrip("/")
        self.clock = clock
        self.stats: Counter[str] = Counter()

    # --- database -------------------------------------------------------
    async def _db(self, action: str, **payload):
        return await run_blocking(self.repository.gateway, action, self.worker, **payload)

    async def _target(
        self, conversation: dict | None, inbox: dict | None, thread: dict | None = None
    ):
        source = conversation or {}
        inbox = inbox or {}
        payload = inbox.get("payload") or {}
        connection = source.get("connection_id") or inbox.get("connection_id")
        account, credential = None, None
        if connection:
            row = await self._db("connection", id=connection, with_secret=True)
            if row:
                account = row["external_id"]
                credential = credentials.unseal(row["secret"]) if row.get("secret") else None
        return Target(
            platform=source.get("platform") or inbox["platform"],
            external_chat_id=source.get("external_chat_id") or inbox["external_chat_id"],
            chat_kind=source.get("chat_kind") or payload.get("chat_kind") or "group",
            connection_id=connection,
            account_id=account,
            credential=credential,
            external_thread_key=(thread or {}).get("external_thread_key")
            or inbox.get("external_thread_key"),
            reply_to=inbox.get("message_id") or payload.get("message_id"),
            asker_external_id=inbox.get("asker_external_id"),
            asker_label=inbox.get("asker_label"),
            via=inbox.get("via") or "message",
            handle={
                key: (inbox.get(key) or payload.get(key))
                for key in (
                    "inline_query_id",
                    "guest_query_id",
                    "callback_query_id",
                    "response_url",
                    "req_id",
                    "message_id",
                )
                if inbox.get(key) or payload.get(key)
            },
        )

    def _open_url(self, project: str, session: str | None) -> str:
        url = f"{self.web_origin}/open/projects/{project}"
        return url + (f"?session={session}" if session else "")

    # --- inbox ----------------------------------------------------------
    async def inbox_once(self) -> bool:
        row = await self._db("claim_inbox", lease_seconds=LEASE_SECONDS)
        if not row:
            return False
        self.stats["claimed"] += 1
        try:
            await self._handle(row)
        except StaleFence:
            self.stats["stale"] += 1
        except Exception:
            logger.exception("chat_app_inbox_failed", extra={"chat_app": {"inbox": row["id"]}})
            self.stats["failed"] += 1
            # Keep the lease: the row comes back when it expires, not in a hot loop.
            if row["attempts"] >= MAX_INBOX_ATTEMPTS:
                with suppress(Exception):
                    await self._finish(row, "done", "error")
        return True

    async def _finish(self, row, state, outcome, **values):
        await self._db(
            "finish_inbox", id=row["id"], fence=row["fence"], state=state, outcome=outcome, **values
        )

    async def _handle(self, row: dict) -> None:
        payload = row.get("payload") or {}
        key = thread_key(row)
        situation = await self._db(
            "context",
            platform=row["platform"],
            connection_id=row.get("connection_id"),
            external_chat_id=row["external_chat_id"],
            external_thread_key=key,
            asker_external_id=row.get("asker_external_id"),
            inbox_id=row["id"],
        )
        decision = decide(row, situation, self.clock())
        conversation = situation.get("conversation")
        platform = self.platforms.get(row["platform"])
        chat = conversation["id"] if conversation else None
        names = {
            "name": (conversation or {}).get("owner_name") or "the owner",
            "workspace": (conversation or {}).get("workspace_name") or "this workspace",
            "mention": "@PuppyOne",
            "asker": row.get("asker_label") or "",
        }
        action = decision.action
        if platform is None:
            await self._finish(row, "done", "platform_off")
            return
        if action == "drop":
            await self._finish(row, "done", decision.outcome, conversation_id=chat)
        elif action == "notice":
            target = await self._target(conversation, row)
            await self._say(platform, target, notice_text(decision.notice, **names))
            await self._finish(row, "done", decision.outcome, conversation_id=chat)
        elif action == "knock":
            await self._knock(row, decision, payload)
        elif action == "joined":
            await self._finish(row, "done", decision.outcome)
        elif action == "removed_there":
            await self._db(
                "removed_there",
                platform=row["platform"],
                connection_id=row.get("connection_id"),
                external_chat_id=row["external_chat_id"],
            )
            await self._finish(row, "done", decision.outcome)
        elif action == "revoke":
            await self._db(
                "revoke_connection", platform=row["platform"], external_id=payload.get("account_id")
            )
            await self._finish(row, "done", decision.outcome)
        elif action == "stop":
            await self._stop(platform, row, decision.values.get("reply_id"))
            await self._finish(row, "done", decision.outcome)
        elif action == "new_thread":
            await self._db(
                "thread", conversation_id=chat, external_thread_key=key, reset_session=True
            )
            await self._finish(row, "done", decision.outcome, conversation_id=chat)
        elif action == "hold":
            raw = codes.link_code()
            await self._db(
                "identity_code",
                platform=row["platform"],
                scope=situation["identity_scope"],
                external_user_id=row["asker_external_id"],
                code_hash=codes.code_hash(raw),
            )
            target = await self._target(conversation, row)
            await self._say(
                platform,
                target,
                notice_text("link_account"),
                button=("Link account", f"{self.web_origin}/chat-apps/link?code={raw}"),
            )
            await self._finish(row, "held", decision.outcome, conversation_id=chat)
        elif action == "wait":
            thread = await self._db("thread", conversation_id=chat, external_thread_key=key)
            sent = await self._say(
                platform, await self._target(conversation, row, thread), notice_text("queued")
            )
            await self._finish(
                row,
                "waiting",
                decision.outcome,
                conversation_id=chat,
                thread_id=thread["id"],
                reply={"notice_code": "queued", "payload": {"sent": self._sent_json(sent)}},
            )
        elif action == "submit":
            await self._submit(row, conversation, decision, names, platform)

    async def _say(self, platform, target, text, *, button=None) -> Sent | None:
        try:
            return await platform.send(target, Message(text, button=button))
        except PlatformError:
            self.stats["send_failed"] += 1
            return None

    @staticmethod
    def _sent_json(sent: Sent | None) -> dict | None:
        return None if sent is None else {"message_id": sent.message_id, "ref": sent.ref}

    async def _knock(self, row, decision, payload) -> None:
        code = decision.values.get("code")
        owner = decision.values.get("owner") or row.get("asker_external_id")
        name = payload.get("chat_title") or (
            "WeCom group"
            if row["platform"] == "wecom"
            else "Direct message"
            if payload.get("chat_kind") == "dm"
            else "Chat"
        )
        result = await self._db(
            "knock",
            platform=row["platform"],
            connection_id=row.get("connection_id"),
            code_hash=codes.code_hash(code) if code else None,
            external_chat_id=row["external_chat_id"],
            chat_kind=payload.get("chat_kind") or "group",
            display_name=name,
            knock_hint=code if row["platform"] == "wecom" else None,
            owner_external_id=owner,
        )
        await self._finish(
            row,
            "done",
            "knocked" if result.get("knocked") else "no_knock",
            conversation_id=result.get("conversation_id"),
        )

    async def _stop(self, platform, row, reply_id) -> None:
        info = await self._db("reply_info", id=reply_id, clicker=row.get("asker_external_id"))
        if not info or not info.get("run_id"):
            return
        clicker = row.get("asker_external_id")
        allowed = clicker and clicker in (
            info.get("asker_external_id"),
            info.get("owner_external_id"),
        )
        allowed = allowed or info.get("clicker_user_id") == info["owner_user_id"]
        target = await self._target(None, row)
        if not allowed:
            text = notice_text(
                "not_your_stop",
                asker=info.get("asker_label") or "the asker",
                name=info.get("owner_name") or "the owner",
            )
            with suppress(PlatformError):
                await platform.notify(target, text)
            return
        try:
            await run_blocking(self.agents.command, info["owner_user_id"], info["run_id"], "stop")
        except HTTPException:
            logger.info("chat_app_stop_refused", extra={"chat_app": {"reply": reply_id}})

    async def _submit(self, row, conversation, decision, names, platform) -> None:
        values = decision.values
        if self.entitlements is not None:
            await run_blocking(
                self.entitlements.observe_feature,
                conversation["org_id"],
                f"chat_app.{row['platform']}",
            )
        thread = await self._db(
            "thread",
            conversation_id=conversation["id"],
            external_thread_key=values["thread_key"],
            reset_session=values.get("fresh_session", False),
        )
        request = SubmitRun(
            project_id=conversation["project_id"],
            session_id=thread.get("agent_session_id"),
            request_id=values["request_id"],
            prompt=values["prompt"],
        )
        try:
            run = await run_blocking(
                self.agents.submit, conversation["owner_user_id"], request, interactive=True
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            code = detail.get("code")
            notice = (
                "unconfigured"
                if code in ("agent_model_unconfigured", "managed_agent_inference_disabled")
                else "no_hours"
                if code == "runtime_credit_unavailable"
                else "paused"
                if exc.status_code in (401, 403)
                else "failed"
            )
            target = await self._target(conversation, row, thread)
            await self._say(platform, target, notice_text(notice, **names))
            await self._finish(
                row,
                "done",
                f"refused:{code or exc.status_code}",
                conversation_id=conversation["id"],
                thread_id=thread["id"],
            )
            return
        if not thread.get("agent_session_id"):
            await self._db(
                "thread",
                conversation_id=conversation["id"],
                external_thread_key=values["thread_key"],
                agent_session_id=run["session_id"],
            )
        await self._finish(
            row,
            "done",
            "submitted",
            conversation_id=conversation["id"],
            thread_id=thread["id"],
            request_id=values["request_id"],
            run_id=run["id"],
            reply={"run_id": run["id"]},
        )
        self.stats["submitted"] += 1

    # --- replies --------------------------------------------------------
    async def reply_once(self) -> bool:
        claimed = await self._db("claim_reply", lease_seconds=LEASE_SECONDS)
        if not claimed:
            return False
        reply = claimed["reply"]
        try:
            await self._relay(claimed)
        except StaleFence:
            self.stats["stale"] += 1
        except Exception:
            logger.exception("chat_app_reply_failed", extra={"chat_app": {"reply": reply["id"]}})
            self.stats["failed"] += 1
            with suppress(Exception):
                await self._save(
                    reply,
                    release=True,
                    attempts=reply["attempts"] + 1,
                    next_attempt_at=(self.clock() + timedelta(seconds=5)).isoformat(),
                )
        return True

    async def _save(self, reply, **values):
        return await self._db(
            "save_reply",
            id=reply["id"],
            fence=reply["fence"],
            lease_seconds=LEASE_SECONDS,
            **values,
        )

    async def _relay(self, claimed: dict) -> None:
        reply, conversation = claimed["reply"], claimed["conversation"]
        thread, inbox = claimed.get("thread"), claimed.get("inbox")
        platform = self.platforms.get(conversation["platform"])
        if platform is None:
            await self._save(reply, state="abandoned", release=True)
            return
        target = await self._target(conversation, inbox, thread)
        names = {
            "name": conversation.get("owner_name") or "the owner",
            "mention": "@PuppyOne",
            "workspace": conversation.get("workspace_name") or "",
        }
        if not reply.get("run_id"):
            await self._notice(platform, target, reply, conversation, names)
            return
        try:
            run = await run_blocking(
                self.agents.snapshot, conversation["owner_user_id"], reply["run_id"]
            )
        except HTTPException:
            # The owner can no longer read the Run: nothing more to show here.
            await self._save(reply, state="abandoned", release=True)
            return
        now = self.clock()
        pacing = platform.pacing(target)
        rendered = render(
            run,
            reply_id=reply["id"],
            owner=names["name"],
            asker=(inbox or {}).get("asker_label") or "",
            open_url=self._open_url(conversation["project_id"], run.get("session_id")),
            pacing=pacing,
            now=now,
        )
        payload = reply.get("payload") or {}
        sent_json = payload.get("sent")
        last_edit = _parse(payload.get("edited_at"))
        fingerprint = digest(rendered.message)
        later = (now + timedelta(seconds=max(1.0, pacing.min_edit_seconds))).isoformat()
        stale = (
            pacing.keepalive_seconds is not None
            and last_edit is not None
            and (now - last_edit).total_seconds() >= pacing.keepalive_seconds
        )
        if fingerprint == reply.get("rendered_digest") and not rendered.finished and not stale:
            await self._save(reply, release=True, next_attempt_at=later)
            return
        if not due(last_edit, pacing, now, rendered.finished):
            await self._save(reply, release=True, next_attempt_at=later)
            return
        try:
            sent = (
                None
                if sent_json is None
                else Sent(sent_json.get("message_id"), sent_json.get("ref") or {})
            )
            try:
                sent = await (
                    platform.edit(target, sent, rendered.message)
                    if sent
                    else platform.send(target, rendered.message)
                )
            except MessageGone:
                sent = await platform.send(target, rendered.message)
        except NotHere:
            await self._save(reply, release=True, next_attempt_at=later)
            return
        except RateLimited as exc:
            self.stats["rate_limited"] += 1
            await self._save(
                reply,
                release=True,
                next_attempt_at=(now + timedelta(seconds=exc.retry_after)).isoformat(),
            )
            return
        except PlatformError:
            self.stats["send_failed"] += 1
            attempts = reply["attempts"] + 1
            # A failed edit is retried; only a final that never lands is given up.
            give_up = rendered.finished and attempts >= FAILED_GIVE_UP
            if give_up:
                logger.warning(
                    "chat_app_final_undelivered", extra={"chat_app": {"reply": reply["id"]}}
                )
            await self._save(
                reply,
                release=True,
                attempts=attempts,
                state="abandoned" if give_up else None,
                next_attempt_at=(now + timedelta(seconds=min(60, 2**attempts))).isoformat(),
            )
            return
        values = {
            "payload": {"sent": self._sent_json(sent), "edited_at": now.isoformat()},
            "rendered_digest": fingerprint,
            "external_message_id": sent.message_id,
            "attempts": 0,
        }
        if rendered.finished:
            if rendered.done_notice and not reply.get("done_notice_sent") and target.via != "guest":
                with suppress(PlatformError):
                    asker = (inbox or {}).get("asker_label") or ""
                    await platform.send(
                        target,
                        Message(f"Done · {asker}".strip(" ·"), mention=target.asker_external_id),
                    )
                values["done_notice_sent"] = True
            await self._save(reply, state="done", units=rendered.units, release=True, **values)
            self.stats["finished"] += 1
            return
        await self._save(reply, release=True, next_attempt_at=later, **values)

    async def _notice(self, platform, target, reply, conversation, names) -> None:
        code = reply["notice_code"]
        if code == "welcome":
            names["name"] = conversation.get("owner_name") or "the owner"
            if conversation["platform"] == "telegram" and conversation["chat_kind"] == "group":
                code = "welcome_telegram"
        try:
            await platform.send(target, Message(notice_text(code, **names)))
            if reply["notice_code"] == "removed":
                with suppress(PlatformError):
                    await platform.leave(target)
        except (NotHere, RateLimited) as exc:
            wait = exc.retry_after if isinstance(exc, RateLimited) else 2
            await self._save(
                reply,
                release=True,
                next_attempt_at=(self.clock() + timedelta(seconds=wait)).isoformat(),
            )
            return
        except PlatformError:
            attempts = reply["attempts"] + 1
            await self._save(
                reply,
                release=True,
                attempts=attempts,
                state="abandoned" if attempts >= FAILED_GIVE_UP else None,
                next_attempt_at=(self.clock() + timedelta(seconds=2**attempts)).isoformat(),
            )
            return
        await self._save(reply, state="done", release=True)

    # --- WeCom long connections -----------------------------------------
    async def connection_once(self, stop: asyncio.Event, tasks: set) -> bool:
        platform = self.platforms.get("wecom")
        if platform is None:
            return False
        row = await self._db("claim_connection", lease_seconds=LEASE_SECONDS * 3)
        if not row:
            return False
        task = asyncio.create_task(self._hold(platform, row, stop))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return True

    async def _hold(self, platform, row, stop) -> None:
        state = {"status": row["status"]}

        async def deliver(event: Inbound) -> None:
            from src.platform.chat_apps.webhook_router import store

            event = Inbound(**{**event.__dict__, "account_id": row["external_id"]})
            await run_blocking(store, self.repository, [event])

        async def heartbeat() -> bool:
            try:
                if state["status"] == "pending":
                    await self._db(
                        "mark_connection", id=row["id"], fence=row["fence"], status="active"
                    )
                    state["status"] = "active"
                await self._db(
                    "heartbeat", id=row["id"], fence=row["fence"], lease_seconds=LEASE_SECONDS * 3
                )
                return True
            except StaleFence:
                return False

        try:
            result = await platform.hold(
                row["external_id"], credentials.unseal(row["secret"]), deliver, heartbeat, stop
            )
        except Exception:
            logger.exception("chat_app_wecom_connection_failed")
            result = "stopped"
        self.stats["wecom_" + result] += 1
        with suppress(StaleFence):
            if result in ("failed", "displaced"):
                await self._db("mark_connection", id=row["id"], fence=row["fence"], status=result)
            else:
                await self._db("release", kind="connection", id=row["id"], fence=row["fence"])

    # --- process --------------------------------------------------------
    async def serve(self, stop: asyncio.Event) -> None:
        holds: set[asyncio.Task] = set()

        async def loop(step, idle):
            while not stop.is_set():
                try:
                    busy = await step()
                except Exception:
                    logger.exception("chat_app_loop_failed")
                    busy = False
                if not busy:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), idle)

        async def stats():
            last_scrub = 0.0
            while not stop.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), STATS_INTERVAL_SECONDS)
                logger.info(
                    "chat_gateway_stats",
                    extra={
                        "chat_gateway": {
                            **dict(self.stats),
                            "wecom_held": len(holds),
                            "worker": self.worker,
                        }
                    },
                )
                self.stats.clear()
                if time.monotonic() - last_scrub > SCRUB_INTERVAL_SECONDS:
                    last_scrub = time.monotonic()
                    with suppress(Exception):
                        await self._db("scrub")

        loops = [
            asyncio.create_task(loop(self.inbox_once, IDLE_SECONDS)),
            asyncio.create_task(loop(self.reply_once, IDLE_SECONDS)),
            asyncio.create_task(loop(lambda: self.connection_once(stop, holds), 10.0)),
            asyncio.create_task(stats()),
        ]
        await stop.wait()
        await asyncio.gather(*loops, return_exceptions=True)
        await asyncio.gather(*holds, return_exceptions=True)
        logger.info("chat_gateway_stopped", extra={"chat_gateway": {"worker": self.worker}})


def preflight(repository: ChatAppRepository) -> None:
    """Exit before claiming if the database lacks the command functions."""
    repository.gateway("scrub", "preflight")


async def main() -> None:
    from src.config import settings
    from src.platform.agent.dependencies import get_agent_service
    from src.platform.chat_apps.dependencies import get_platforms
    from src.platform.entitlements.service import EntitlementService
    from src.platform.project.write_lease import install_storage_write_admission
    from src.platform.repository_policy import load_repository_policy

    # Same startup contract as every deployed process, though Runs are written by agent_worker.
    install_storage_write_admission()
    load_repository_policy()
    repository = ChatAppRepository()
    await run_blocking(preflight, repository)
    platforms = get_platforms()
    gateway = Gateway(
        repository,
        get_agent_service(),
        platforms,
        EntitlementService(),
        web_origin=settings.FRONTEND_URL,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for value in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(value, stop.set)
    if not platforms:
        logger.info("chat_gateway_idle", extra={"chat_gateway": {"reason": "no platforms"}})
        await stop.wait()
        return
    await gateway.serve(stop)


if __name__ == "__main__":
    from src.utils.logging_setup import setup_logging

    setup_logging()
    asyncio.run(main())
