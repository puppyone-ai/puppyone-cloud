"""Backend-only atomic access lifecycle commands and consistent authority reads."""

from fastapi import HTTPException
from postgrest.exceptions import APIError

from src.infra.supabase.dependencies import get_supabase_client


class AccessRepository:
    def __init__(self, client=None):
        self.client = client if client is not None else get_supabase_client()

    def call(self, name, **values):
        try:
            result = (
                self.client.rpc(name, {"p_" + key: value for key, value in values.items()})
                .execute()
                .data
            )
        except APIError as exc:
            # No database messages, principal IDs or credential material cross HTTP.
            status = {
                "42501": 403,
                "22023": 422,
                "23514": 422,
                "23505": 409,
                "23503": 409,
                "55000": 409,
            }.get(str(exc.code), 503)
            code = (
                "access_unavailable"
                if status == 503
                else "access_denied"
                if status == 403
                else "access_invalid_state"
            )
            raise HTTPException(status, detail={"code": code}) from exc
        if isinstance(result, dict) and result.get("error"):
            code = result["error"]
            if code not in {"access_invalid_credential", "access_credential_reused"}:
                code = "access_denied"
            raise HTTPException(401, detail={"code": code})
        return result


class GrantRepository(AccessRepository):
    def command(self, action, user, **payload):
        return self.call("access_grant_command", action=action, user=user, payload=payload)

    def context(self, grant, channel, session=None):
        return self.call("access_grant_context", grant=grant, channel=channel, session=session)
