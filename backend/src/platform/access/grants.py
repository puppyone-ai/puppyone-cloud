"""Common invitation, approval and revocation workflows for every access adapter."""


from src.infra.execution.runner import run_blocking
from src.platform.authorization.factory import build_authorization_service
from src.shared_types.grants import ProjectAction

from .credential_repository import access_token_hash, access_token_hash_candidates
from .grant_repository import GrantRepository


class GrantService:
    def __init__(self, repository=None, authorization=None):
        self.repository = repository or GrantRepository()
        self.authorization = authorization or build_authorization_service(self.repository.client)

    async def manage(self, action, user, project, **payload):
        await run_blocking(
            self.authorization.authorize, project, user, ProjectAction.CREDENTIAL_MANAGE
        )
        result = await run_blocking(
            self.repository.command, action, user, project_id=project, **payload
        )
        if action == "grant_state" and payload.get("state") == "revoked" and isinstance(result, dict):
            await self._revoke_host_export_if_needed(result.get("workbench_id"))
        return result

    async def _revoke_host_export_if_needed(self, workbench_id):
        if not workbench_id:
            return
        try:
            from .composition import build_workbench_service

            workspaces = build_workbench_service()
            row = await run_blocking(workspaces.repository.get, workbench_id)
            if (
                isinstance(row, dict)
                and row.get("collaboration_mode") == "fly_host_export"
                and row.get("host_workbench_id")
            ):
                await workspaces.lifecycle.revoke_host_export_agent(row)
        except Exception:
            # Best-effort immediate tunnel revocation; DB grant revocation already fences shells.
            return

    async def invite(self, user, project, request):
        values = request.model_dump(mode="json", exclude={"secret"})
        values["key_hash"] = access_token_hash(request.secret.get_secret_value())
        return await self.manage("invite", user, project, **values)

    async def inspect(self, collection, user, project):
        await run_blocking(
            self.authorization.authorize, project, user, ProjectAction.CREDENTIAL_MANAGE
        )
        return await run_blocking(
            self.repository.call, "access_inspect", user=user,
            collection=collection, project=project,
        )

    async def request(self, user, request):
        return await run_blocking(
            self.repository.command,
            "request",
            user,
            id=str(request.id),
            registration_id=str(request.registration_id),
            key_hashes=access_token_hash_candidates(request.invitation.get_secret_value()),
        )

    async def authorize(self, grant, channel, *, session=None, project=None, workbench=None):
        facts = await run_blocking(self.repository.context, grant, channel, session)
        return self.authorization.authorize_delegated(
            facts, channel, project=project, workbench=workbench
        )
