"""The HTTPS command service: what it refuses before anything runs, and how a command ends."""

import asyncio
import logging
import time

import pytest

from src.platform.access.adapters import shell_session
from src.platform.access.adapters.https import commands as service_module
from src.platform.access.adapters.https.commands import (
    MAX_RUNNING,
    AgentCommands,
    CommandRefused,
    SeenSignatures,
)
from src.platform.access.adapters.https.signature import command_message
from src.platform.workspace.errors import WorkspaceError

from .support import new_key, public_text, sshsig

CONNECTION = "1fa1d8ab-79bb-4688-9dd6-bebc8afcd33d"
IDENTITY = f"agent-{CONNECTION}"


class Access:
    """The Workspace side: admission, the session's heartbeat and its end."""

    def __init__(self, admit_error=None):
        self.admit_error = admit_error
        self.admitted, self.finished = [], []

    async def admit(self, identity, notify=None):
        if notify:
            await notify("Preparing your workspace; this can take a few minutes.\r\n")
        if self.admit_error:
            raise self.admit_error
        self.admitted.append(identity)
        return {"id": identity}

    async def renew_session(self, session):
        pass

    async def finish(self, session):
        self.finished.append(session["id"])


class Agent:
    def __init__(self):
        self.key = new_key()
        self.public = public_text(self.key)

    def request(self, command, moment=None):
        moment = int(time.time()) if moment is None else moment
        message = command_message(CONNECTION, moment, command)
        return str(moment), sshsig(self.key, message)


def service(access=None, admitted=True, admits=None):
    agent = Agent()
    calls = []

    async def approved(connection, key):
        calls.append((connection, key))
        if isinstance(admitted, Exception):
            raise admitted
        return admitted if key == agent.public else False

    commands = AgentCommands(
        access or Access(), seen=SeenSignatures(url=""), admits=admits or approved
    )
    return commands, agent, calls


async def refusal(commands, agent, command, **request):
    moment, signature = agent.request(command)
    moment = request.get("moment", moment)
    signature = request.get("signature", signature)
    with pytest.raises(CommandRefused) as caught:
        await commands.check(CONNECTION, moment, signature, request.get("body", command))
    return caught.value


async def read(commands, command="echo ok"):
    return b"".join([chunk async for chunk in commands.output(CONNECTION, command)]).decode()


def running(*steps, status=0):
    """A ``run_command`` that writes ``steps`` (bytes, or an exception to raise) and returns."""
    seen = []

    async def run(access, admission, session, command, *, write, notify, disconnected=None):
        seen.append((session, command))
        for step in steps:
            if isinstance(step, BaseException):
                raise step
            if isinstance(step, float):
                await asyncio.sleep(step)
            else:
                await write("stdout", step)
        return status

    run.seen = seen
    return run


async def test_an_approved_fresh_request_is_admitted_with_its_command_text():
    commands, agent, calls = service()
    moment, signature = agent.request("echo 你好".encode())
    assert await commands.check(CONNECTION, moment, signature, "echo 你好".encode()) == "echo 你好"
    assert calls == [(CONNECTION, agent.public)]


async def test_a_request_refused_on_its_own_terms_never_asks_who_was_approved():
    commands, agent, calls = service()
    wrong = Agent()
    for command, request, code, status in (
        (
            b"echo ok",
            {"signature": wrong.request(b"echo other")[1]},
            "agent_signature_invalid",
            401,
        ),
        (b"echo ok", {"moment": str(int(time.time()) - 3600)}, "agent_clock_skew", 401),
        (b"echo ok", {"moment": ""}, "agent_signature_invalid", 401),
        (b"\xff\xfe", {}, "agent_command_invalid", 413),
        (b"x" * 32769, {}, "agent_command_invalid", 413),
    ):
        refused = await refusal(commands, agent, command, **request)
        assert (refused.code, refused.status) == (code, status)
        assert refused.message
    assert calls == []


async def test_a_busy_process_refuses_before_reading_the_signature():
    commands, agent, calls = service()
    commands.running = MAX_RUNNING
    refused = await refusal(commands, agent, b"echo ok")
    assert (refused.code, refused.status) == ("agent_commands_busy", 503)
    assert calls == []


async def test_a_key_or_approval_that_does_not_hold_is_forbidden():
    for admitted in (False, None, "yes"):
        commands, agent, _ = service(admitted=admitted)
        refused = await refusal(commands, agent, b"echo ok")
        assert (refused.code, refused.status) == ("access_denied", 403)
    commands, _, _ = service()
    stranger = Agent()
    refused = await refusal(commands, stranger, b"echo ok")
    assert refused.code == "access_denied"


async def test_an_admission_that_cannot_be_checked_is_unavailable_not_forbidden(caplog):
    commands, agent, _ = service(admitted=RuntimeError("database down"))
    refused = await refusal(commands, agent, b"echo ok")
    assert (refused.code, refused.status) == ("agent_commands_unavailable", 503)
    assert "database down" not in refused.message
    assert "agent_exec_admission_unavailable" in caplog.text


async def test_a_signed_request_runs_once():
    commands, agent, _ = service()
    moment, signature = agent.request(b"echo once")
    assert await commands.check(CONNECTION, moment, signature, b"echo once") == "echo once"
    with pytest.raises(CommandRefused) as caught:
        await commands.check(CONNECTION, moment, signature, b"echo once")
    assert (caught.value.code, caught.value.status) == ("agent_signature_replayed", 409)
    # The same command signed again is a new request.
    moment, signature = agent.request(b"echo once", moment=int(time.time()) + 1)
    assert await commands.check(CONNECTION, moment, signature, b"echo once") == "echo once"


async def test_output_streams_then_ends_with_the_exit_status_on_its_own_line(monkeypatch):
    run = running(b"first\n", b"no newline", status=7)
    monkeypatch.setattr(service_module, "run_command", run)
    access = Access()
    commands, _, _ = service(access)
    out = await read(commands, "make")
    assert out == (
        "PuppyOne: Preparing your workspace; this can take a few minutes.\n"
        "first\nno newline\n[exit 7]\n"
    )
    assert run.seen == [({"id": IDENTITY}, "make")]
    assert access.admitted == access.finished == [IDENTITY]
    assert commands.running == 0


async def test_a_machine_still_being_prepared_says_so_and_runs_nothing(monkeypatch):
    run = running()
    monkeypatch.setattr(service_module, "run_command", run)
    access = Access(admit_error=WorkspaceError(503, {"code": "workspace_preparing"}))
    commands, _, _ = service(access)
    out = await read(commands)
    assert out.endswith(
        "PuppyOne: the workspace is still being prepared; run the command again in a minute.\n"
        "[exit 1]\n"
    )
    assert run.seen == []
    assert access.finished == []


async def test_access_ended_mid_command_says_why(monkeypatch):
    run = running(b"partial", WorkspaceError(403, {"code": "access_denied"}))
    monkeypatch.setattr(service_module, "run_command", run)
    access = Access()
    commands, _, _ = service(access)
    out = await read(commands)
    assert out.endswith(
        "partialPuppyOne: access to this workspace is not available: it was revoked, expired or"
        " ended by the owner, or is already in use by another session.\n[exit 1]\n"
    )
    assert access.finished == [IDENTITY]


async def test_an_unexpected_fault_tells_the_agent_without_internals(monkeypatch, caplog):
    run = running(RuntimeError("database password=hunter2"))
    monkeypatch.setattr(service_module, "run_command", run)
    access = Access()
    commands, _, _ = service(access)
    out = await read(commands)
    assert out.endswith(f"PuppyOne: {shell_session.UNEXPECTED_ENDING}\n[exit 255]\n")
    assert "hunter2" not in out
    assert "agent_exec_failed" in caplog.text
    assert access.finished == [IDENTITY]


async def test_a_command_past_its_time_is_stopped_and_says_so(monkeypatch):
    monkeypatch.setattr(service_module, "COMMAND_SECONDS", 0.2)
    run = running(b"working\n", 5.0)
    monkeypatch.setattr(service_module, "run_command", run)
    access = Access()
    commands, _, _ = service(access)
    async with asyncio.timeout(5):
        out = await read(commands)
    assert out.endswith(
        "PuppyOne: the command ran longer than 0 minutes and was stopped.\n[exit 124]\n"
    )
    assert access.finished == [IDENTITY]


async def test_a_timeout_inside_the_workspace_is_not_called_the_time_limit(monkeypatch):
    run = running(TimeoutError())
    monkeypatch.setattr(service_module, "run_command", run)
    commands, _, _ = service()
    out = await read(commands)
    assert out.endswith("[exit 255]\n")
    assert "ran longer than" not in out


async def test_seen_signatures_forget_a_request_once_its_window_has_passed(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(service_module.time, "monotonic", lambda: clock[0])
    seen = SeenSignatures(url="")
    assert await seen.first("a") is True
    assert await seen.first("a") is False
    assert await seen.first("b") is True
    clock[0] += 2 * service_module.SKEW_SECONDS + 1
    assert await seen.first("a") is True


class Redis:
    def __init__(self, fail=False):
        self.fail, self.calls, self.closed, self.keys = fail, [], 0, set()

    async def set(self, key, value, *, nx, ex):
        self.calls.append((key, value, nx, ex))
        if self.fail:
            raise ConnectionError("redis down")
        if key in self.keys:
            return None
        self.keys.add(key)
        return True

    async def aclose(self):
        self.closed += 1


async def test_replicas_share_what_they_have_seen_through_redis(monkeypatch):
    import redis.asyncio as redis

    client = Redis()
    monkeypatch.setattr(redis, "from_url", lambda url: client)
    first, other_replica = SeenSignatures(url="redis://x"), SeenSignatures(url="redis://x")
    assert await first.first("c:digest") is True
    assert await other_replica.first("c:digest") is False
    assert client.calls[0] == ("agent-exec:c:digest", 1, True, 2 * service_module.SKEW_SECONDS)
    assert client.closed == 2


async def test_an_unreachable_redis_still_refuses_a_replay_here(monkeypatch, caplog):
    import redis.asyncio as redis

    monkeypatch.setattr(redis, "from_url", lambda url: Redis(fail=True))
    seen = SeenSignatures(url="redis://x")
    with caplog.at_level(logging.WARNING):
        assert await seen.first("c:digest") is True
        assert await seen.first("c:digest") is False
    assert "agent_exec_replay_store_unavailable" in caplog.text
