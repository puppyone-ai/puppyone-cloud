"""Authenticated Access control plane and the connector's narrow renewal endpoint."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser

from .credentials import CredentialService
from .grants import GrantService
from .lifecycle_schemas import (
    AccessApplication,
    AccessDecision,
    CredentialRenewal,
    InvitationCreate,
    RegistrationCreate,
    RegistrationKeyReset,
    StateChange,
)
from .registrations import RegistrationService


class AccessRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def redacted_validation(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Pydantic validation errors can contain the raw secret input,
                # including unknown body keys. Never reflect or log that body.
                raise HTTPException(422, detail={"code": "access_invalid_request"}) from exc

        return redacted_validation


router = APIRouter(prefix="/api/v1", tags=["access"], route_class=AccessRoute)


def verified_user(user: CurrentUser = Depends(get_current_user)):
    if user.is_anonymous:
        raise HTTPException(403, detail={"code": "access_verified_identity_required"})
    return user.user_id


def registration_service():
    return RegistrationService()


def grant_service():
    return GrantService()


def credential_service():
    return CredentialService()


@router.post("/access/registrations")
async def register(
    payload: RegistrationCreate, user=Depends(verified_user), service=Depends(registration_service)
):
    return await service.create(user, payload)


@router.get("/access/registrations")
async def registrations(user=Depends(verified_user), service=Depends(registration_service)):
    return await service.list(user)


@router.get("/access/registrations/{registration_id}/grants")
async def registered_grants(
    registration_id: UUID, user=Depends(verified_user), service=Depends(registration_service)
):
    return await service.grants(user, str(registration_id))


@router.patch("/access/registrations/{registration_id}")
async def registration_state(
    registration_id: UUID,
    payload: StateChange,
    user=Depends(verified_user),
    service=Depends(registration_service),
):
    return await service.change_state(user, str(registration_id), payload.state)


@router.put("/access/registrations/{registration_id}/credential")
async def reset_registration_key(
    registration_id: UUID,
    payload: RegistrationKeyReset,
    user=Depends(verified_user),
    service=Depends(registration_service),
):
    return await service.reset_key(user, str(registration_id), payload)


@router.post("/access/requests")
async def apply(
    payload: AccessApplication, user=Depends(verified_user), service=Depends(grant_service)
):
    return await service.request(user, payload)


@router.post("/projects/{project_id}/access/invitations")
async def invite(
    project_id: str,
    payload: InvitationCreate,
    user=Depends(verified_user),
    service=Depends(grant_service),
):
    return await service.invite(user, project_id, payload)


@router.get("/projects/{project_id}/access/{collection}")
async def list_access(
    project_id: str,
    collection: Literal["invitations", "requests", "grants", "sessions"],
    user=Depends(verified_user),
    service=Depends(grant_service),
):
    return await service.manage(collection, user, project_id)


@router.post("/projects/{project_id}/access/requests/{request_id}/decision")
async def decide(
    project_id: str,
    request_id: UUID,
    payload: AccessDecision,
    user=Depends(verified_user),
    service=Depends(grant_service),
):
    values = payload.model_dump(mode="json", exclude_none=True)
    return await service.manage("decide", user, project_id, id=str(request_id), **values)


@router.delete("/projects/{project_id}/access/invitations/{invitation_id}")
async def revoke_invitation(
    project_id: str,
    invitation_id: UUID,
    user=Depends(verified_user),
    service=Depends(grant_service),
):
    return await service.manage("revoke_invitation", user, project_id, id=str(invitation_id))


@router.patch("/projects/{project_id}/access/grants/{grant_id}")
async def grant_state(
    project_id: str,
    grant_id: UUID,
    payload: StateChange,
    user=Depends(verified_user),
    service=Depends(grant_service),
):
    return await service.manage(
        "grant_state", user, project_id, id=str(grant_id), state=payload.state
    )


@router.delete("/projects/{project_id}/access/sessions/{session_id}")
async def close_session(
    project_id: str, session_id: UUID, user=Depends(verified_user), service=Depends(grant_service)
):
    return await service.manage("close_session", user, project_id, id=str(session_id))


@router.post("/access/credentials/renew")
async def renew(
    payload: CredentialRenewal,
    authorization: str = Header(default=""),
    service=Depends(credential_service),
):
    scheme, _, secret = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secret:
        raise HTTPException(401, detail={"code": "access_invalid_credential"})
    endpoint = None
    if payload.channel == "ssh":
        from .adapters.ssh.hosted import ssh_endpoint

        endpoint = ssh_endpoint()  # Validate deployment before rotating the caller's key.
    receipt = await service.renew(secret, payload)
    if endpoint:
        receipt = {
            **receipt,
            **endpoint,
            "username": receipt["id"],
            "command": f"ssh -p {endpoint['port']} {receipt['id']}@{endpoint['host']}",
        }
    return receipt
