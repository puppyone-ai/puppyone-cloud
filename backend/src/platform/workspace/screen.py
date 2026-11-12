"""The Cloud Computer's screen: a one-time ticket, then a byte bridge.

A renderer cannot put the user's session token on a WebSocket, and it never
holds that token. It first asks the authenticated API for a ticket; the ticket
is an HMAC-signed, single-use, minute-long capability for exactly one user,
workspace and generation. The WebSocket presents it as a subprotocol, so it
never reaches a URL or an access log.

The bridge copies bytes between the WebSocket and the provider's stream to the
in-machine relay; it never parses the RFB protocol. While open it records
workspace activity (so idle pause waits) and re-checks authorization, the
generation and the owner's toggle, ending the stream when any changes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from contextlib import suppress

from src.config import settings
from src.infra.execution.runner import run_blocking
from src.platform.sandbox import binding_profile, resolve_features, runtime_provider
from src.shared_types.grants import ProjectAction

from .errors import WorkspaceError
from .execution.worker_port import WorkerDisconnected

# Ordinary ways a stream ends; the adapter adds its transport's disconnect type.
_ENDINGS = (WorkspaceError, WorkerDisconnected, ConnectionError)

AUDIENCE = "workspace-screen"
SUBPROTOCOL = "puppyone.screen.v1"
TICKET_PREFIX = "puppyone.screen.ticket."
TICKET_SECONDS = 60
HEARTBEAT_SECONDS = 60
READ_LIMIT = 256 * 1024


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class ScreenTickets:
    """HMAC tickets keyed apart from every other token signed with JWT_SECRET."""

    def __init__(self, secret: str | None = None, clock=time.time):
        secret = secret if secret is not None else settings.JWT_SECRET
        if not secret:
            raise RuntimeError("JWT_SECRET is required to sign screen tickets")
        self._key = hmac.new(secret.encode(), AUDIENCE.encode(), hashlib.sha256).digest()
        self._clock = clock
        self._used: dict[str, float] = {}

    def _sign(self, payload: str) -> str:
        return _b64e(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())

    def issue(self, row: dict) -> tuple[str, int]:
        expires = int(self._clock()) + TICKET_SECONDS
        claims = {
            "aud": AUDIENCE,
            "sub": row["user_id"],
            "project": row["project_id"],
            "workbench": row["id"],
            "generation": row["generation"],
            "exp": expires,
            "nonce": secrets.token_urlsafe(16),
        }
        payload = _b64e(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        return f"{payload}.{self._sign(payload)}", expires

    def redeem(self, token: str, project: str) -> dict:
        """Verify once. A replay, a forgery or another project's ticket fails alike."""
        try:
            payload, signature = token.split(".", 1)
            if not hmac.compare_digest(signature, self._sign(payload)):
                raise ValueError
            claims = json.loads(_b64d(payload))
        except (ValueError, TypeError, json.JSONDecodeError):
            raise PermissionError("screen_ticket_invalid") from None
        now = self._clock()
        self._used = {nonce: exp for nonce, exp in self._used.items() if exp > now}
        if (
            claims.get("aud") != AUDIENCE
            or claims.get("project") != project
            or not isinstance(claims.get("exp"), int)
            or claims["exp"] <= now
            or claims.get("nonce") in self._used
        ):
            raise PermissionError("screen_ticket_invalid")
        self._used[claims["nonce"]] = claims["exp"]
        return claims


def screen_feature(row: dict) -> dict:
    """The resolved ``screen_stream`` Feature of a workspace row."""
    resolved = resolve_features(binding_profile(row.get("binding")), row.get("sandbox_settings"))
    return next(item for item in resolved if item["id"] == "screen_stream")


def ticket_from_protocols(header: str) -> str | None:
    for raw in (header or "").split(","):
        value = raw.strip()
        if value.startswith(TICKET_PREFIX):
            return value[len(TICKET_PREFIX) :]
    return None


class WorkspaceScreen:
    def __init__(self, service, tickets: ScreenTickets | None = None):
        self.service = service
        self.tickets = tickets or ScreenTickets()

    async def _admitted(self, user: str, project: str) -> tuple[dict, dict]:
        context, _ = await run_blocking(
            self.service.context, user, project, ProjectAction.AGENT_RUN
        )
        row = context["workbench"]
        if row is None:
            raise WorkspaceError(404, {"code": "workbench_not_found"})
        return row, context

    @staticmethod
    def _require_stream(row: dict) -> None:
        if row["state"] != "running":
            raise WorkspaceError(409, {"code": "workbench_not_running", "state": row["state"]})
        feature = screen_feature(row)
        if not feature["enabled"] or feature["preview"]:
            raise WorkspaceError(
                409, {"code": "screen_unavailable", "reason": feature["reason"] or "preview"}
            )
        provider = runtime_provider((row.get("binding") or {}).get("provider"))
        if "screen" not in provider.capabilities.streams:
            raise WorkspaceError(409, {"code": "screen_unavailable", "reason": "unsupported"})

    async def session(self, user: str, project: str) -> dict:
        row, _ = await self._admitted(user, project)
        self._require_stream(row)
        # The running image must declare the capability, not only the Profile.
        await self.service.connection(row, capabilities={"screen_stream"})
        ticket, expires = self.tickets.issue(row)
        return {
            "ticket": ticket,
            "protocol": SUBPROTOCOL,
            "ticket_protocol_prefix": TICKET_PREFIX,
            "transport": screen_feature(row)["transport"],
            "expires_at": expires,
        }

    async def admit(self, project: str, ticket: str) -> tuple[dict, dict]:
        """Redeem a ticket and re-establish authority for the bridge."""
        claims = self.tickets.redeem(ticket, project)
        row, context = await self._admitted(claims["sub"], project)
        if row["id"] != claims["workbench"] or row["generation"] != claims["generation"]:
            raise WorkspaceError(409, {"code": "workbench_generation_changed"})
        self._require_stream(row)
        return row, context

    async def bridge(self, websocket, row: dict, context: dict, *, endings=()) -> None:
        host = self.service.channel_factory(row)
        stream = None
        try:
            host.connection = await host.runtime.running_connection(host)
            stream = await host.runtime.open_stream(host, "screen")
            await self._pump(websocket, stream, row, (*_ENDINGS, *endings))
        finally:
            if stream is not None:
                with suppress(Exception):
                    await stream.close()
            with suppress(Exception):
                await host.runtime.aclose()

    async def _pump(self, websocket, stream, row: dict, endings: tuple) -> None:
        async def upstream():
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                data = message.get("bytes")
                if data:
                    await stream.write(data)

        async def downstream():
            while data := await stream.read(READ_LIMIT):
                await websocket.send_bytes(data)

        async def heartbeat():
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                current, context = await self._admitted(row["user_id"], row["project_id"])
                if current["id"] != row["id"] or current["generation"] != row["generation"]:
                    return
                self._require_stream(current)
                await run_blocking(self.service.repository.activity, current, context["revision"])

        tasks = [asyncio.create_task(job()) for job in (upstream, downstream, heartbeat)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                # A revoked authority, a closed viewer or a changed toggle ends
                # the stream like a disconnect; anything else is a fault.
                error = task.exception()
                if error and not isinstance(error, endings):
                    raise error
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
