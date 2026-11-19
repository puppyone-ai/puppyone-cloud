"""One HTTPS request runs one command in a linked Agent's own Workspace.

The request is signed with the Agent's SSH key and admitted exactly as an SSH
connection is: the same ticket, Access session, shell and cleanup. It reaches
an Agent wherever plain HTTPS does, including sandboxes whose only way out is
an HTTP proxy, which an SSH connection cannot use without help.
"""

import asyncio
import hashlib
import logging
import time
from contextlib import suppress

from src.config import settings

from ..shell_session import (
    MAX_COMMAND_BYTES,
    SESSION_MESSAGES,
    UNEXPECTED_ENDING,
    ClientDisconnected,
    ConnectionAdmission,
    run_command,
    session_ending,
)
from .signature import SKEW_SECONDS, SignatureRefused, check_time, command_message, signing_key

logger = logging.getLogger(__name__)

AGENT_USER_PREFIX = "agent-"
# A request holds a slot in this process for as long as its command runs.
COMMAND_SECONDS = 600
MAX_RUNNING = 64
TIMED_OUT = 124
# A quiet command writes nothing that would fail, so whether its client left is asked.
DISCONNECT_CHECK_SECONDS = 2
# Proxies on the way close a response that sends nothing for a while (Railway's
# edge at 5 minutes, sandbox proxies sooner), so a quiet command says it runs.
QUIET_NOTE_SECONDS = 30
# Commands still stopping after their client left; held so the loop keeps them.
_COMMANDS: set[asyncio.Task] = set()
REFUSALS = {
    "agent_signature_invalid": (
        401,
        "this request is not signed by the key this connection was approved with; "
        "sign it with ~/.ssh/puppyone_agent.",
    ),
    "agent_clock_skew": (
        401,
        "this computer's clock is more than 5 minutes off; correct it and run the command again.",
    ),
    "agent_signature_replayed": (
        409,
        "this signed request was already used; sign the command again to run it again.",
    ),
    "agent_command_invalid": (
        413,
        f"the command must be UTF-8 text of at most {MAX_COMMAND_BYTES} bytes; "
        "put a long script in a file first.",
    ),
    "access_denied": (403, SESSION_MESSAGES["access_denied"]),
    "agent_commands_busy": (503, "too many commands are running; run it again in a few seconds."),
    "agent_commands_unavailable": (503, "PuppyOne could not check this request; try again."),
}


class CommandRefused(Exception):
    """Refused before the command started: an HTTP status and words for the Agent."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.status, self.message = REFUSALS[code]


class SeenSignatures:
    """Each signed request runs once; Redis shares that across API replicas."""

    def __init__(self, url=None):
        self.url = settings.NOTIFICATIONS_REDIS_URL if url is None else url
        self.local = {}

    async def first(self, key):
        if self.url:
            import redis.asyncio as redis

            client = redis.from_url(self.url)
            try:
                return bool(await client.set(f"agent-exec:{key}", 1, nx=True, ex=2 * SKEW_SECONDS))
            except Exception:
                # The clock window still bounds a replay; never refuse every command.
                logger.warning("agent_exec_replay_store_unavailable")
            finally:
                await client.aclose()
        now = time.monotonic()
        self.local = {seen: until for seen, until in self.local.items() if until > now}
        if key in self.local:
            return False
        self.local[key] = now + 2 * SKEW_SECONDS
        return True


class _Request:
    """What ``ConnectionAdmission`` closes: the response ends with its command."""

    def close(self):
        pass


class AgentCommands:
    def __init__(self, access, seen=None, admits=None):
        self.access = access
        self.seen = seen or SeenSignatures()
        self._admits = admits
        self.running = 0

    async def admits(self, connection, key):
        if self._admits is not None:
            return await self._admits(connection, key)
        return await self.access.linked_agents().admits(connection, key)

    async def check(self, connection, timestamp, signature, command):
        """Refuse before anything streams, so a refusal keeps its HTTP status."""
        if self.running >= MAX_RUNNING:
            raise CommandRefused("agent_commands_busy")
        try:
            if len(command) > MAX_COMMAND_BYTES:
                raise UnicodeError("Command too large")
            text = command.decode("utf-8")
            moment = check_time(timestamp)
            key = signing_key(signature, command_message(connection, moment, command))
        except UnicodeError as exc:
            raise CommandRefused("agent_command_invalid") from exc
        except SignatureRefused as exc:
            raise CommandRefused(exc.code) from exc
        try:
            admitted = await self.admits(connection, key)
        except Exception as exc:
            logger.warning("agent_exec_admission_unavailable", exc_info=True)
            raise CommandRefused("agent_commands_unavailable") from exc
        if admitted is not True:
            raise CommandRefused("access_denied")
        digest = hashlib.sha256(signature.encode()).hexdigest()
        if not await self.seen.first(f"{connection}:{digest}"):
            raise CommandRefused("agent_signature_replayed")
        return text

    async def output(self, connection, command, is_disconnected=None):
        """The command's output as it comes, then ``[exit N]`` on a line of its own.

        The command runs in a task of its own. When the client leaves, the
        response is cancelled, but the command is only told to stop: stopping
        its shell and finishing its session must not be cut short.
        ``is_disconnected()`` finds a client that left while nothing was written.
        """
        queue = asyncio.Queue(maxsize=64)
        done = object()
        gone = asyncio.Event()

        async def write(data):
            if not gone.is_set():
                await queue.put(data)

        async def disconnected():
            while not gone.is_set():
                if is_disconnected and await is_disconnected():
                    break
                with suppress(TimeoutError):
                    await asyncio.wait_for(gone.wait(), DISCONNECT_CHECK_SECONDS)
            raise ClientDisconnected("HTTPS client disconnected")

        async def produce():
            try:
                await self._run(connection, command, write, gone, disconnected)
            finally:
                self.running -= 1
                if not gone.is_set():
                    await queue.put(done)

        self.running += 1
        task = asyncio.create_task(produce())
        _COMMANDS.add(task)
        task.add_done_callback(_COMMANDS.discard)
        try:
            while (chunk := await queue.get()) is not done:
                yield chunk
        finally:
            gone.set()
            # Free a write waiting on a full queue, so the command can notice and end.
            while not queue.empty():
                queue.get_nowait()

    async def _run(self, connection, command, write, gone, disconnected):
        last = b"\n"
        written = started = time.monotonic()

        async def emit(data):
            nonlocal last, written
            if data:
                last, written = data[-1:], time.monotonic()
                await write(data)

        async def quiet_notes():
            """A line of its own whenever the command has said nothing for a while."""
            while True:
                await asyncio.sleep(max(0.0, written + QUIET_NOTE_SECONDS - time.monotonic()))
                if time.monotonic() - written >= QUIET_NOTE_SECONDS:
                    running = int(time.monotonic() - started)
                    gap = b"" if last == b"\n" else b"\n"
                    await emit(gap + f"PuppyOne: still running ({running}s)...\n".encode())

        async def notify(message):
            await emit(f"PuppyOne: {message.replace(chr(13), '')}".encode())

        async def relay(stream, data):
            await emit(data)

        admission = ConnectionAdmission(self.access, _Request())
        admission.begin()
        status = 255
        deadline = asyncio.timeout(COMMAND_SECONDS)
        logger.info("agent_exec_started", extra={"connection": connection})
        try:
            async with deadline:
                session = await admission.session_for(AGENT_USER_PREFIX + connection, notify)
                if gone.is_set():
                    # It left while its machine was prepared: nothing is run for nobody.
                    return
                notes = asyncio.create_task(quiet_notes())
                try:
                    status = await run_command(
                        self.access,
                        admission,
                        session,
                        command,
                        write=relay,
                        notify=notify,
                        disconnected=disconnected,
                    )
                finally:
                    notes.cancel()
                    with suppress(asyncio.CancelledError):
                        await notes
        except Exception as exc:
            code, hung_up = session_ending(exc)
            if hung_up:
                # Nobody is reading: the shell has stopped and the session is finished.
                logger.info("agent_exec_client_disconnected", extra={"connection": connection})
                return
            if isinstance(exc, TimeoutError) and deadline.expired():
                status = TIMED_OUT
                message = f"the command ran longer than {int(COMMAND_SECONDS // 60)} minutes and was stopped."
            elif code:
                # Revoked, replaced, preparing or busy: expected and actionable.
                logger.warning("agent_exec_refused", extra={"connection": connection, "code": code})
                status, message = 1, SESSION_MESSAGES.get(code, code)
            else:
                logger.exception("agent_exec_failed", extra={"connection": connection})
                message = UNEXPECTED_ENDING
            await notify(message + "\n")
        finally:
            admission.end()
            await admission.close()
        if last != b"\n":
            await write(b"\n")
        await write(f"[exit {status}]\n".encode())
