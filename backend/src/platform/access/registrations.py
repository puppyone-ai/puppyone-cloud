"""Verified owners manage durable client registrations; only hashes reach storage."""


from src.infra.execution.runner import run_blocking

from .credential_repository import access_token_hash
from .registration_repository import RegistrationRepository


class RegistrationService:
    def __init__(self, repository=None):
        self.repository = repository or RegistrationRepository()

    async def create(self, user, request):
        return await run_blocking(
            self.repository.command,
            "create",
            user,
            id=str(request.id),
            name=request.name,
            owner_org_id=request.owner_org_id,
            key_hash=access_token_hash(request.secret.get_secret_value()),
        )

    async def list(self, user):
        return await run_blocking(self.repository.command, "list", user)

    async def grants(self, user, identity):
        return await run_blocking(self.repository.command, "grants", user, id=identity)

    async def requests(self, user, identity):
        return await run_blocking(
            self.repository.call, "access_inspect", user=user,
            collection="requests", registration=identity,
        )

    async def change_state(self, user, identity, state):
        return await run_blocking(
            self.repository.command, "state", user, id=identity, state=state
        )

    async def reset_key(self, user, identity, request):
        return await run_blocking(
            self.repository.command,
            "reset_key",
            user,
            id=identity,
            key_hash=access_token_hash(request.secret.get_secret_value()),
        )
