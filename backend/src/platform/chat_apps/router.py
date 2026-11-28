"""Desktop routes for one project's chats. Every route is in the authorization manifest."""

from uuid import UUID

from fastapi import APIRouter, Depends

from src.platform.auth.dependencies import get_current_user
from src.platform.authorization.dependencies import require_project_action
from src.platform.chat_apps.dependencies import get_chat_app_service
from src.platform.chat_apps.schemas import ChangeChat, ConnectBot, Decision, StartLink
from src.shared_types.grants import ProjectAction

router = APIRouter(prefix="/projects/{project_id}/chat-apps", tags=["chat-apps"])
READ = Depends(require_project_action(ProjectAction.AGENT_READ))
MANAGE = Depends(require_project_action(ProjectAction.AGENT_MANAGE))


@router.get("", dependencies=[READ])
def chats(project_id: str, user=Depends(get_current_user), service=Depends(get_chat_app_service)):
    return service.list(user.user_id, project_id)


@router.post("/links", dependencies=[MANAGE])
def start(
    project_id: str,
    body: StartLink,
    user=Depends(get_current_user),
    service=Depends(get_chat_app_service),
):
    return service.start(user.user_id, project_id, body)


@router.get("/links/{link_id}", dependencies=[MANAGE])
def link(
    project_id: str,
    link_id: UUID,
    user=Depends(get_current_user),
    service=Depends(get_chat_app_service),
):
    return service.link_status(user.user_id, project_id, str(link_id))


@router.post("/connections", dependencies=[MANAGE])
def connect(
    project_id: str,
    body: ConnectBot,
    user=Depends(get_current_user),
    service=Depends(get_chat_app_service),
):
    return service.connect(user.user_id, project_id, body)


@router.post("/conversations/{conversation_id}/decision", dependencies=[MANAGE])
def decide(
    project_id: str,
    conversation_id: UUID,
    body: Decision,
    user=Depends(get_current_user),
    service=Depends(get_chat_app_service),
):
    return service.decide(user.user_id, project_id, str(conversation_id), body.decision)


@router.patch("/conversations/{conversation_id}", dependencies=[MANAGE])
def change(
    project_id: str,
    conversation_id: UUID,
    body: ChangeChat,
    user=Depends(get_current_user),
    service=Depends(get_chat_app_service),
):
    return service.change(user.user_id, project_id, str(conversation_id), body)


@router.post("/conversations/{conversation_id}/owner", dependencies=[MANAGE])
def take_over(
    project_id: str,
    conversation_id: UUID,
    user=Depends(get_current_user),
    service=Depends(get_chat_app_service),
):
    return service.take_over(user.user_id, project_id, str(conversation_id))


@router.delete("/conversations/{conversation_id}", dependencies=[MANAGE])
def remove(
    project_id: str,
    conversation_id: UUID,
    user=Depends(get_current_user),
    service=Depends(get_chat_app_service),
):
    return service.remove(user.user_id, project_id, str(conversation_id))
