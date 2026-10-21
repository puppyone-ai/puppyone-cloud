"""WebSocket router for product version notifications.

* Auth via ``Authorization: Bearer <token>`` header on the upgrade
  request — same Bearer token PuppyOne already accepts on REST routes.
* On successful auth + upgrade, register the connection with the
  process-wide :class:`NotificationManager`, flush any offline events,
  then keep the connection open until the peer closes.
* Each ``commit_update`` event is JSON and shaped by
  :class:`NotificationManager`.

We intentionally don't implement subscription filtering inside the
client connection (e.g. "only send me notifications for path X");
filtering happens server-side via the auth context's scope. A client
authenticated against scope ``docs`` only ever sees events that touch
``docs`` or its descendants.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState

from src.infra.supabase.client import SupabaseClient
from src.version_engine.admission.identity import PuppyOneAuthenticator
from src.platform.repository_target.auth_context import repository_view_from_auth
from src.version_engine.derived.notifications import NotificationManager
from src.utils.logger import log_debug, log_error

ws_router = APIRouter(prefix="/api/v1/version")


_BEARER_SUBPROTOCOL_PREFIX = "version.bearer."
_ACCEPT_SUBPROTOCOL = "version.v1"


def _extract_ws_credentials(websocket: WebSocket) -> tuple[str, str | None]:
    """Pull the auth token + (optional) accept-subprotocol off the upgrade.

    Order of precedence:

    1. ``Authorization: Bearer <token>`` header (CLI / Node clients).
    2. ``Sec-WebSocket-Protocol: version.bearer.<token>`` — the only viable
       path from a browser, since ``new WebSocket(url, ...)`` cannot
       set arbitrary headers. We must echo back *some* subprotocol so
       the handshake completes; we choose a benign ``version.v1`` rather
       than echoing the bearer one (which would leak the JWT into the
       response ``Sec-WebSocket-Protocol`` header that proxies log).
    3. ``?token=<jwt>`` query string (last-resort fallback; access logs
       must scrub query strings on this route).

    Returns ``(token, accept_subprotocol_or_None)``. ``token == ""`` ⇒
    caller should close 1008.
    """
    # 1. Authorization header.
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip(), None

    # 2. Subprotocol — browser-native path.
    protocols_header = websocket.headers.get("sec-websocket-protocol", "")
    for raw in protocols_header.split(","):
        proto = raw.strip()
        if proto.startswith(_BEARER_SUBPROTOCOL_PREFIX):
            token = proto[len(_BEARER_SUBPROTOCOL_PREFIX):]
            if token:
                return token, _ACCEPT_SUBPROTOCOL

    # 3. Query string fallback.
    return (websocket.query_params.get("token") or "").strip(), None


@ws_router.websocket("/{project_id}/ws")
async def version_ws(websocket: WebSocket, project_id: str):
    """Server-push WebSocket — client subscribes; server fans out
    ``commit_update`` events as pushes happen.

    See :func:`_extract_ws_credentials` for the auth contract. Optional
    ``X-PuppyOne-User`` header / ``user`` query param scopes the audit
    identity to a specific agent / user.
    """
    token, accept_subprotocol = _extract_ws_credentials(websocket)
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION,
                              reason="missing token (header / subprotocol / query)")
        return

    user_identity = (
        websocket.headers.get("x-puppyone-user")
        or websocket.query_params.get("user")
        or ""
    )

    try:
        authenticator = PuppyOneAuthenticator(SupabaseClient())
        auth = authenticator.authenticate(
            token, project_id, user_identity=user_identity,
        )
    except Exception as e:
        log_error(f"[VersionWS] auth failed for project={project_id}: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION,
                              reason="auth failed")
        return

    scope_path = repository_view_from_auth(auth).path_prefix
    agent = auth.get("agent", "")

    if accept_subprotocol:
        await websocket.accept(subprotocol=accept_subprotocol)
    else:
        await websocket.accept()
    # Debug-level: a successful upgrade is the happy path and there can
    # be many of them per session (one per tab navigation in dev). Keep
    # auth-failure paths at info/error so problems still stand out.
    log_debug(
        f"[VersionWS] connected project={project_id} agent={agent} "
        f"scope={scope_path!r}"
    )

    manager = NotificationManager.get()
    conn = await manager.register(websocket, project_id, scope_path, agent)
    try:
        # Drain any queued offline events first so the client isn't
        # missing what arrived while they were disconnected.
        await manager.flush_offline(conn)

        # Keep the connection open. Receiving frames is mostly so we
        # notice client-side closes promptly; we don't currently act
        # on inbound text frames (no client→server protocol yet).
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                # Block on receive_text so we get an immediate
                # WebSocketDisconnect rather than busy-looping.
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        await manager.unregister(conn)
        log_debug(f"[VersionWS] disconnected project={project_id} agent={agent}")
