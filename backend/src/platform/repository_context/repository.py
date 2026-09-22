"""Persistence for repository targets and user-owned Git credentials."""

from __future__ import annotations

from typing import Any

from src.platform.repository_context.models import GitCredentialMode
from src.platform.repository_target.models import (
    RepositoryTarget,
    repository_target_scope_id,
)
from src.repo.access_credentials import (
    HASH_ALG,
    access_token_hash,
    access_token_metadata,
)
from src.utils.id_generator import generate_uuid_v7


class RepositoryContextRepository:
    def __init__(self, supabase_client: Any | None = None):
        if supabase_client is None:
            from src.infra.supabase.dependencies import get_supabase_client

            supabase_client = get_supabase_client()
        self._client = supabase_client

    def get_scope(self, project_id: str, scope_id: str) -> dict[str, Any] | None:
        rows = (
            self._client.table("repository_scopes")
            .select("*")
            .eq("project_id", project_id)
            .eq("id", scope_id)
            .limit(1)
            .execute()
        ).data or []
        return rows[0] if rows else None

    def issue_user_git_credential(
        self,
        *,
        operation_key: str,
        payload_hash: str,
        org_id: str,
        target: RepositoryTarget,
        user_id: str,
        mode: GitCredentialMode,
        raw_token: str,
    ) -> dict:
        """Idempotently publish a client-generated, hash-only credential."""

        credential_id = generate_uuid_v7()
        access_surface_id = generate_uuid_v7()
        key_prefix, key_last4 = access_token_metadata(raw_token)
        response = self._client.rpc(
            "issue_user_git_http_credential_idempotent",
            {
                "p_operation_key": operation_key,
                "p_payload_hash": payload_hash,
                "p_credential_id": credential_id,
                "p_access_surface_id": access_surface_id,
                "p_org_id": org_id,
                "p_project_id": target.project_id,
                "p_scope_id": repository_target_scope_id(target),
                "p_user_id": user_id,
                "p_grant_mode": mode.value,
                "p_key_prefix": key_prefix,
                "p_key_last4": key_last4,
                "p_key_hash": access_token_hash(raw_token),
                "p_hash_alg": HASH_ALG,
            },
        ).execute()
        data = response.data
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, dict):
            raise RuntimeError("Git credential issue RPC returned an invalid response")
        return data

    def revoke_user_git_credential(
        self,
        *,
        credential_id: str,
        project_id: str,
        user_id: str,
    ) -> bool:
        response = self._client.rpc(
            "revoke_user_git_http_credential",
            {
                "p_credential_id": credential_id,
                "p_project_id": project_id,
                "p_user_id": user_id,
            },
        ).execute()
        return response.data is True
