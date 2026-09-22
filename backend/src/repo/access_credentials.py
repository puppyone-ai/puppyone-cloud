"""Access surface credential hashing and persistence."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from src.config import settings


HASH_ALG = "hmac_sha256_v1"
CREDENTIALS_TABLE = "access_surface_credentials"


def generate_access_token(prefix: str) -> str:
    clean_prefix = (prefix or "key").strip().strip("_") or "key"
    return f"{clean_prefix}_{secrets.token_urlsafe(32)}"


def access_token_hash(raw_token: str) -> str:
    secret = settings.ACCESS_CREDENTIAL_HASH_SECRET
    if not secret:
        raise ValueError("ACCESS_CREDENTIAL_HASH_SECRET is required to hash access credentials")
    return hmac.new(
        secret.encode("utf-8"),
        raw_token.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def access_token_hash_candidates(raw_token: str) -> list[str]:
    """Accept one explicitly retained old key without issuing new hashes with it."""
    candidates = [access_token_hash(raw_token)]
    previous = settings.ACCESS_CREDENTIAL_PREVIOUS_HASH_SECRET
    if previous and previous != settings.ACCESS_CREDENTIAL_HASH_SECRET:
        candidates.append(hmac.new(
            previous.encode("utf-8"), raw_token.strip().encode("utf-8"), hashlib.sha256,
        ).hexdigest())
    return candidates


def access_token_metadata(raw_token: str) -> tuple[str, str]:
    token = raw_token.strip()
    prefix = token.split("_", 1)[0] if "_" in token else token[:3]
    return prefix, token[-4:] if len(token) >= 4 else token


def mask_access_token(prefix: str | None, last4: str | None) -> str:
    if not prefix or not last4:
        return ""
    return f"{prefix}_{'•' * 8}{last4}"


class AccessCredentialRepository:
    def __init__(self, supabase_client=None):
        if supabase_client is None:
            from src.infra.supabase.dependencies import get_supabase_client

            self._client = get_supabase_client()
        else:
            self._client = supabase_client

    def list_active_by_surface(self, surface_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not surface_ids:
            return {}
        resp = (
            self._client.table(CREDENTIALS_TABLE)
            .select("*")
            .in_("access_surface_id", surface_ids)
            .in_("credential_type", ["bearer_token", "git_http_token"])
            .eq("status", "active")
            .eq("credential_lifecycle", "shared")
            .execute()
        )
        rows = resp.data or []
        now = datetime.now(timezone.utc)
        by_surface: dict[str, dict[str, Any]] = {}
        for row in rows:
            expires_at = row.get("expires_at")
            if expires_at and datetime.fromisoformat(
                str(expires_at).replace("Z", "+00:00")
            ) <= now:
                continue
            by_surface.setdefault(row["access_surface_id"], row)
        return by_surface

    def get_active_by_token(
        self,
        raw_token: str,
        *,
        credential_type: str = "bearer_token",
    ) -> Optional[dict[str, Any]]:
        matched = []
        for token_hash in access_token_hash_candidates(raw_token):
            resp = (
                self._client.table(CREDENTIALS_TABLE)
                .select("*")
                .eq("key_hash", token_hash)
                .eq("credential_type", credential_type)
                .eq("status", "active")
                .limit(1)
                .execute()
            )
            if resp.data:
                matched = resp.data
                break
        now = datetime.now(timezone.utc)
        rows = [
            row for row in matched
            if not row.get("expires_at")
            or datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00")) > now
        ]
        if not rows:
            return None
        credential = rows[0]
        if credential.get("credential_lifecycle") == "user":
            user_mode = self._user_credential_mode_if_authorized(credential)
            if user_mode is None:
                return None
            credential = {**credential, "user_credential_mode": user_mode}
        return credential

    def resolve_git_runtime_credential(
        self, raw_token: str
    ) -> Optional[dict[str, Any]]:
        """Resolve a Git HTTP token into already-narrowed runtime facts.

        Production uses one SECURITY DEFINER RPC so credential, surface,
        scope, credential owner, and current human access are evaluated from a single
        database snapshot.  The table-backed branch exists only for the
        lightweight in-memory repository used by unit tests.
        """

        rpc = getattr(self._client, "rpc", None)
        if callable(rpc):
            for token_hash in access_token_hash_candidates(raw_token):
                response = rpc(
                    "resolve_git_runtime_credential",
                    {"p_key_hash": token_hash},
                ).execute()
                rows = response.data or []
                if rows:
                    return rows if isinstance(rows, dict) else rows[0]
            return None

        credential = self.get_active_by_token(
            raw_token,
            credential_type="git_http_token",
        )
        if not credential:
            return None
        surfaces = (
            self._client.table("access_surfaces")
            .select("*")
            .eq("id", credential["access_surface_id"])
            .eq("project_id", credential["project_id"])
            .eq("status", "active")
            .limit(1)
            .execute()
        ).data or []
        if not surfaces or surfaces[0].get("kind") != "git_remote":
            return None
        surface = surfaces[0]
        if str(surface.get("org_id")) != str(credential.get("org_id")):
            return None
        scope_id = surface.get("scope_id")
        scope: dict[str, Any] | None = None
        if scope_id is not None:
            scopes = (
                self._client.table("repository_scopes")
                .select("*")
                .eq("id", scope_id)
                .eq("project_id", credential["project_id"])
                .limit(1)
                .execute()
            ).data or []
            if not scopes:
                return None
            scope = scopes[0]
        target_max_mode = str((scope or {}).get("max_mode") or "rw")
        modes = {
            str(credential.get("grant_mode") or target_max_mode),
            target_max_mode,
            str((surface.get("config") or {}).get("mode") or "rw"),
        }
        user_mode = credential.get("user_credential_mode")
        if user_mode:
            modes.add(str(user_mode))
        effective_mode = "r" if "r" in modes else "rw"
        return {
            "credential_id": str(credential["id"]),
            "org_id": str(credential["org_id"]),
            "project_id": str(credential["project_id"]),
            "access_surface_id": str(surface["id"]),
            "target_kind": "scope" if scope is not None else "project_root",
            "scope_id": str(scope["id"]) if scope is not None else None,
            "path_prefix": str((scope or {}).get("path") or ""),
            "excludes": (scope or {}).get("exclude") or [],
            "target_max_mode": target_max_mode,
            "user_id": credential.get("user_id"),
            "effective_mode": effective_mode,
        }

    def _user_credential_mode_if_authorized(
        self, credential: dict[str, Any]
    ) -> str | None:
        """Re-evaluate a user credential against current Project access.

        Membership removal and role downgrade take effect on the next Git
        request. No local checkout, device, or workspace identity participates.
        """
        try:
            user_id = credential.get("user_id")
            if not user_id:
                return None
            surfaces = (
                self._client.table("access_surfaces")
                .select("project_id, status")
                .eq("id", credential["access_surface_id"])
                .eq("status", "active")
                .limit(1)
                .execute()
            ).data or []
            if not surfaces:
                return None
            surface = surfaces[0]
            if str(surface["project_id"]) != str(credential["project_id"]):
                return None

            from src.platform.authorization.models import ProjectAction
            from src.platform.authorization.repository import AuthorizationRepository
            from src.platform.authorization.service import AuthorizationService

            authorization = AuthorizationService(AuthorizationRepository(self._client))
            project_id = str(credential["project_id"])
            if authorization.allows(project_id, str(user_id), ProjectAction.CONTENT_WRITE):
                return "rw"
            if authorization.allows(project_id, str(user_id), ProjectAction.CONTENT_READ):
                return "r"
            return None
        except Exception:
            return None

    def get_active_by_surface(self, access_surface_id: str) -> Optional[dict[str, Any]]:
        resp = (
            self._client.table(CREDENTIALS_TABLE)
            .select("*")
            .eq("access_surface_id", access_surface_id)
            .eq("credential_type", "bearer_token")
            .eq("status", "active")
            .eq("credential_lifecycle", "shared")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        row = rows[0]
        expires_at = row.get("expires_at")
        if expires_at and datetime.fromisoformat(
            str(expires_at).replace("Z", "+00:00")
        ) <= datetime.now(timezone.utc):
            return None
        return row

    def store_bearer_token(
        self,
        *,
        access_surface_id: str,
        org_id: str | None,
        project_id: str,
        raw_token: str,
        created_by: str | None = None,
        revoke_existing: bool = True,
        expires_at: datetime | None = None,
    ) -> None:
        """Persist a supplied bearer token as hash-only credential state.

        This is used by the idempotent legacy backfill and by compatibility
        callers that already generated a token. New product flows should call
        :meth:`issue_bearer_token` so generation stays inside this boundary.
        """
        if not revoke_existing and expires_at is None:
            raise ValueError("non-rotating bearer session credentials require expires_at")
        if revoke_existing:
            rpc = getattr(self._client, "rpc", None)
            if callable(rpc):
                key_prefix, key_last4 = access_token_metadata(raw_token)
                rpc("rotate_access_surface_bearer_token", {
                    "p_access_surface_id": access_surface_id,
                    "p_org_id": org_id,
                    "p_project_id": project_id,
                    "p_key_prefix": key_prefix,
                    "p_key_last4": key_last4,
                    "p_key_hash": access_token_hash(raw_token),
                    "p_hash_alg": HASH_ALG,
                    "p_created_by": created_by,
                    "p_expires_at": (
                        expires_at.astimezone(timezone.utc).isoformat()
                        if expires_at is not None else None
                    ),
                }).execute()
                return
            # Lightweight repositories used by local tests do not implement
            # RPC. Production Supabase always takes the transactional path.
            self.revoke_active(
                access_surface_id,
                credential_type="bearer_token",
                credential_lifecycle="shared",
            )

        key_prefix, key_last4 = access_token_metadata(raw_token)
        payload = {
            "org_id": org_id,
            "project_id": project_id,
            "access_surface_id": access_surface_id,
            "credential_type": "bearer_token",
            "credential_lifecycle": "shared" if revoke_existing else "session",
            "key_prefix": key_prefix,
            "key_last4": key_last4,
            "key_hash": access_token_hash(raw_token),
            "hash_alg": HASH_ALG,
            "status": "active",
            "created_by": created_by,
        }
        if expires_at is not None:
            payload["expires_at"] = expires_at.astimezone(timezone.utc).isoformat()
        self._client.table(CREDENTIALS_TABLE).insert(payload).execute()

    def issue_bearer_token(
        self,
        *,
        access_surface_id: str,
        org_id: str | None,
        project_id: str,
        prefix: str,
        created_by: str | None = None,
        revoke_existing: bool = True,
        expires_at: datetime | None = None,
    ) -> str:
        token = generate_access_token(prefix)
        self.store_bearer_token(
            access_surface_id=access_surface_id,
            org_id=org_id,
            project_id=project_id,
            raw_token=token,
            created_by=created_by,
            revoke_existing=revoke_existing,
            expires_at=expires_at,
        )
        return token

    def store_git_http_token(
        self,
        *,
        access_surface_id: str,
        org_id: str,
        project_id: str,
        raw_token: str,
        grant_mode: str,
        created_by: str | None = None,
        revoke_existing: bool = True,
        expires_at: datetime | None = None,
    ) -> None:
        if grant_mode not in {"r", "rw"}:
            raise ValueError("grant_mode must be 'r' or 'rw'")
        if not revoke_existing and expires_at is None:
            raise ValueError("non-rotating Git session credentials require expires_at")
        key_prefix, key_last4 = access_token_metadata(raw_token)
        if revoke_existing:
            rpc = getattr(self._client, "rpc", None)
            if callable(rpc):
                rpc(
                    "rotate_access_surface_git_http_token",
                    {
                        "p_access_surface_id": access_surface_id,
                        "p_org_id": org_id,
                        "p_project_id": project_id,
                        "p_grant_mode": grant_mode,
                        "p_key_prefix": key_prefix,
                        "p_key_last4": key_last4,
                        "p_key_hash": access_token_hash(raw_token),
                        "p_hash_alg": HASH_ALG,
                        "p_created_by": created_by,
                        "p_expires_at": (
                            expires_at.astimezone(timezone.utc).isoformat()
                            if expires_at is not None
                            else None
                        ),
                    },
                ).execute()
                return
            self.revoke_active(
                access_surface_id,
                credential_type="git_http_token",
                grant_mode=grant_mode,
                credential_lifecycle="shared",
            )

        payload = {
            "org_id": org_id,
            "project_id": project_id,
            "access_surface_id": access_surface_id,
            "credential_type": "git_http_token",
            "grant_mode": grant_mode,
            "credential_lifecycle": "shared" if revoke_existing else "session",
            "key_prefix": key_prefix,
            "key_last4": key_last4,
            "key_hash": access_token_hash(raw_token),
            "hash_alg": HASH_ALG,
            "status": "active",
            "created_by": created_by,
        }
        if expires_at is not None:
            payload["expires_at"] = expires_at.astimezone(timezone.utc).isoformat()
        self._client.table(CREDENTIALS_TABLE).insert(payload).execute()

    def issue_git_http_token(
        self,
        *,
        access_surface_id: str,
        org_id: str,
        project_id: str,
        grant_mode: str,
        prefix: str = "git",
        created_by: str | None = None,
        revoke_existing: bool = True,
        expires_at: datetime | None = None,
    ) -> str:
        token = generate_access_token(prefix)
        self.store_git_http_token(
            access_surface_id=access_surface_id,
            org_id=org_id,
            project_id=project_id,
            raw_token=token,
            grant_mode=grant_mode,
            created_by=created_by,
            revoke_existing=revoke_existing,
            expires_at=expires_at,
        )
        return token

    def revoke_active(
        self,
        access_surface_id: str,
        *,
        credential_type: str | None = None,
        grant_mode: str | None = None,
        credential_lifecycle: str | None = None,
    ) -> None:
        patch = {"status": "revoked", "revoked_at": datetime.now(timezone.utc).isoformat()}
        query = (
            self._client.table(CREDENTIALS_TABLE)
            .update(patch)
            .eq("access_surface_id", access_surface_id)
            .eq("status", "active")
        )
        if credential_type:
            query = query.eq("credential_type", credential_type)
        if grant_mode:
            query = query.eq("grant_mode", grant_mode)
        if credential_lifecycle:
            query = query.eq("credential_lifecycle", credential_lifecycle)
        query.execute()
