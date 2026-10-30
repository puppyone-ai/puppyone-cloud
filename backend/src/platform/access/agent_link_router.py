"""Agent links: the owner's control plane and the two calls an Agent makes itself."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import PlainTextResponse

from .agent_links import INVITATION_PATTERN, AgentLinkService, agent_instructions
from .lifecycle_router import AccessRoute, verified_user
from .lifecycle_schemas import AgentConnect, AgentConnectionDecision, AgentLinkCreate

router = APIRouter(prefix="/api/v1", tags=["access"], route_class=AccessRoute)
instructions_router = APIRouter(tags=["access"])


def agent_link_service():
    return AgentLinkService()


@instructions_router.get("/agent/{invitation}", include_in_schema=False)
def instructions(invitation: str):
    """What an Agent reads. It reveals nothing beyond the link it was fetched with."""
    if not INVITATION_PATTERN.fullmatch(invitation):
        raise HTTPException(404)
    return PlainTextResponse(
        agent_instructions(invitation),
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex",
        },
    )


@router.post("/projects/{project_id}/agent-links")
async def create_link(
    project_id: str,
    payload: AgentLinkCreate,
    user=Depends(verified_user),
    service=Depends(agent_link_service),
):
    return await service.create(user, project_id, payload)


@router.get("/projects/{project_id}/agent-links")
async def links(project_id: str, user=Depends(verified_user), service=Depends(agent_link_service)):
    return await service.inventory(user, project_id)


@router.delete("/projects/{project_id}/agent-links")
async def stop_sharing(
    project_id: str, user=Depends(verified_user), service=Depends(agent_link_service)
):
    return await service.stop(user, project_id)


@router.get("/agent-links/connections/{connection_id}/review")
async def review(
    connection_id: UUID, user=Depends(verified_user), service=Depends(agent_link_service)
):
    return await service.review(user, str(connection_id))


@router.post("/agent-links/connections/{connection_id}/decision")
async def decide(
    connection_id: UUID,
    payload: AgentConnectionDecision,
    user=Depends(verified_user),
    service=Depends(agent_link_service),
):
    return await service.decide(user, str(connection_id), payload.decision)


@router.post("/agent-links/connect")
async def connect(
    payload: AgentConnect, response: Response, service=Depends(agent_link_service)
):
    response.headers["Cache-Control"] = "no-store"
    return await service.connect(payload)


@router.get("/agent-links/connections/{connection_id}")
async def status(
    connection_id: UUID,
    response: Response,
    authorization: str = Header(default=""),
    service=Depends(agent_link_service),
):
    scheme, _, secret = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secret:
        raise HTTPException(401, detail={"code": "access_invalid_credential"})
    response.headers["Cache-Control"] = "no-store"
    return await service.status(str(connection_id), secret)
