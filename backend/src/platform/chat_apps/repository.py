"""The only database access of chat apps: the two command functions. Plain ``def``."""

from fastapi import HTTPException
from postgrest.exceptions import APIError

from src.infra.supabase.dependencies import get_supabase_client

# Fixed codes raised by our own SQL; safe to return to clients.
ACTIONABLE = frozenset(
    {
        "chat_app_conflict",
        "chat_app_connection_taken",
        "chat_app_connection_not_ready",
        "chat_app_link_expired",
    }
)
STATUS = {"42501": 403, "22023": 422, "55000": 409, "53400": 409, "23505": 409}


class StaleFence(Exception):
    """Another gateway instance owns this row now."""


class ChatAppRepository:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = get_supabase_client()
        return self._client

    def _rpc(self, name, args):
        try:
            return self.client.rpc(name, args).execute().data
        except APIError as exc:
            if exc.message == "chat_app_stale_fence":
                raise StaleFence() from exc
            status = STATUS.get(str(exc.code), 503)
            code = (
                exc.message
                if exc.message in ACTIONABLE
                else {
                    403: "chat_app_denied",
                    422: "chat_app_invalid_request",
                    409: "chat_app_conflict",
                }.get(status, "chat_app_unavailable")
            )
            raise HTTPException(status, detail={"code": code}) from exc

    def human(self, action: str, user: str, **payload):
        return self._rpc(
            "chat_app_command", {"p_action": action, "p_user": user, "p_payload": payload}
        )

    def gateway(self, action: str, worker: str, **payload):
        return self._rpc(
            "chat_app_gateway_command",
            {"p_action": action, "p_worker": worker, "p_payload": payload},
        )
