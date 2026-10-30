"""Git/CLI credential resolution and legacy Scope credential admission.

Access owns the shared credential repository. This resolver accepts the existing
Git HTTP credential type; MCP keeps its own bearer/surface admission path.
"""

import re

import asyncssh
from fastapi import HTTPException

from src.infra.execution.runner import run_blocking
from src.infra.supabase.client import SupabaseClient
from src.platform.access.models import ResolvedScopeCredential
from src.platform.authorization.models import RuntimeGrant, RuntimeMode, RuntimePrincipal
from src.platform.repository_target.models import ResolvedRepositoryView


def resolve_access_point(access_key: str, *, channel: str = "git") -> tuple[str, dict]:
    from src.infra.supabase.client import SupabaseClient
    from src.platform.access.credential_repository import AccessCredentialRepository
    from src.platform.repository_target.models import ProjectRootTarget

    try:
        resolved = AccessCredentialRepository(
            SupabaseClient().client
        ).resolve_git_runtime_credential(access_key, channel=channel)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Repository credential lookup unavailable"
        ) from exc
    if not resolved:
        raise HTTPException(status_code=401, detail="Invalid repository credential")
    if resolved.get("target_kind") != "project_root" or resolved.get("scope_id") is not None:
        raise HTTPException(status_code=501, detail={"code": "native_scope_not_available"})
    if resolved.get("path_prefix") or resolved.get("excludes"):
        raise HTTPException(status_code=403, detail="Invalid Project-root view")
    project_id = resolved["project_id"]
    target = ProjectRootTarget(project_id=project_id)
    grant = RuntimeGrant(
        principal=RuntimePrincipal(
            principal_id=resolved.get("registration_id") or resolved["credential_id"],
            credential_kind="git_http_token",
            credential_id=resolved["credential_id"],
            access_grant_id=resolved.get("access_grant_id"),
        ),
        target=target,
        repository_view=ResolvedRepositoryView(
            target=target, path_prefix="", excludes=(), max_mode=resolved["target_max_mode"]
        ),
        mode=RuntimeMode(resolved["effective_mode"]),
    )
    return project_id, {
        "agent": "git-credential:" + resolved["credential_id"],
        "_runtime_grant": grant,
        "_credential_id": resolved["credential_id"],
        "_access_surface_id": resolved["access_surface_id"],
        "_credential_user_id": resolved.get("user_id"),
        "_user_identity": "",
    }


def resolve_scope_access_credential(
    supabase: SupabaseClient,
    access_key: str,
) -> ResolvedScopeCredential | None:
    """Resolve the existing Scope credential chain without owning its geometry.

    Storage failures propagate. Only an invalid credential/Surface/Scope chain
    returns None; callers retain their existing fail-closed admission policy.
    """
    from dataclasses import replace

    from src.platform.access.surface_repository import AccessSurfaceRepository
    from src.platform.repository_target.scope_repository import RepositoryScopeRepository

    credential = AccessSurfaceRepository(supabase.client).resolve_scope_credential(access_key)
    if credential is None:
        return None
    scope = RepositoryScopeRepository(supabase.client).get(credential.scope_id)
    if scope is None or scope.project_id != credential.project_id:
        return None
    if credential.mode_ceiling == "r" and scope.max_mode == "rw":
        scope = replace(scope, max_mode="r")
    return ResolvedScopeCredential(
        credential_id=credential.credential_id,
        credential_type=credential.credential_type,
        access_surface_id=credential.access_surface_id,
        scope=scope,
    )


def normalize_ssh_public_key(value):
    """Canonical public verifier; private keys and authorized_keys options are rejected."""
    try:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if not isinstance(value, str) or not re.fullmatch(
            r"ssh-ed25519[ \t]+[A-Za-z0-9+/]+={0,3}(?:[ \t]+[^\r\n]*)?", value.strip()
        ):
            raise ValueError("Ed25519 public key required")
        key = asyncssh.import_public_key(value)
        if key.get_algorithm() != "ssh-ed25519":
            raise ValueError("Ed25519 public key required")
        # A comment (``user@host``) is not part of the key. Keys received over
        # SSH carry none, so a comment-sensitive verifier would reject a key a
        # user pasted from ``~/.ssh/id_ed25519.pub``.
        key.set_comment(None)
        return key.export_public_key().decode().strip()
    except (ValueError, asyncssh.KeyImportError) as exc:
        raise ValueError("Ed25519 public key required") from exc


class CredentialService:
    """Connector renewal exchanges a registered identity for narrow protocol proof."""

    def __init__(self, repository=None):
        from .grant_repository import AccessRepository

        self.repository = repository or AccessRepository()

    async def renew(self, secret, request):
        from .credential_repository import access_token_hash, access_token_hash_candidates

        if not re.fullmatch(r"par_[A-Za-z0-9_-]{43,128}", secret):
            raise HTTPException(401, detail={"code": "access_invalid_credential"})
        try:
            public_key = (
                normalize_ssh_public_key(request.public_key) if request.public_key else None
            )
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "ssh_ed25519_public_key_required"}) from exc
        key_hashes = access_token_hash_candidates(secret)
        try:
            return await self._issue(key_hashes, request, public_key)
        except HTTPException as exc:
            if str(request.channel) == "ssh" and exc.detail == {
                "code": "access_workspace_unavailable"
            }:
                await self._start_workspace(key_hashes, request)
            raise

    async def _start_workspace(self, key_hashes, request):
        """Record the intent and return: the client retries once the machine is up."""
        row = await run_blocking(
            self.repository.call,
            "access_registered_workspace",
            key_hashes=key_hashes,
            grant=str(request.grant_id),
        )
        if row["state"] != "running":
            from .composition import build_workbench_service

            await build_workbench_service().request_open(row["user_id"], row["project_id"], row)

    async def _issue(self, key_hashes, request, public_key):
        from .credential_repository import access_token_hash

        result = await run_blocking(
            self.repository.call,
            "access_issue_credential",
            key_hashes=key_hashes,
            next_key_hash=access_token_hash(request.next_secret.get_secret_value()),
            request=str(request.request_id),
            grant=str(request.grant_id),
            channel=str(request.channel),
            credential_hash=access_token_hash(request.credential_secret.get_secret_value())
            if request.credential_secret
            else None,
            public_key=public_key,
            surface=request.surface_id,
            ttl=request.expires_in,
        )
        return result
