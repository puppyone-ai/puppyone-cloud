from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.office.dependencies import get_office_service
from src.platform.office.models import (
    OfficeAvailabilityResponse,
    OfficeCallbackBody,
    OfficeCloseResponse,
    OfficeEditorSessionResponse,
    OfficeForceSaveResponse,
    OfficeSessionStateResponse,
)
from src.platform.office.service import ManagedOfficeError, ManagedOfficeService

router = APIRouter(prefix="/office", tags=["managed-office"])


@router.get("/availability", response_model=OfficeAvailabilityResponse)
async def get_availability(
    _current_user: CurrentUser = Depends(get_current_user),
    service: ManagedOfficeService = Depends(get_office_service),
) -> OfficeAvailabilityResponse:
    return service.availability()


@router.post("/sessions", response_model=OfficeEditorSessionResponse, status_code=201)
async def create_session(
    file: Annotated[UploadFile, File(...)],
    locale: Annotated[str, Form()] = "en",
    current_user: CurrentUser = Depends(get_current_user),
    service: ManagedOfficeService = Depends(get_office_service),
) -> OfficeEditorSessionResponse:
    try:
        content = await _read_upload(file, service)
        display_name = current_user.email or "PuppyOne user"
        return await service.create_session(
            owner_user_id=current_user.user_id,
            filename=file.filename or "",
            content=content,
            locale=locale,
            display_name=display_name,
        )
    except ManagedOfficeError as exc:
        raise _http_error(exc) from exc
    finally:
        await file.close()


@router.get("/sessions/{session_id}", response_model=OfficeSessionStateResponse)
async def get_session_state(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: ManagedOfficeService = Depends(get_office_service),
) -> OfficeSessionStateResponse:
    try:
        return await service.get_state(owner_user_id=current_user.user_id, session_id=session_id)
    except ManagedOfficeError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/force-save", response_model=OfficeForceSaveResponse)
async def force_save(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: ManagedOfficeService = Depends(get_office_service),
) -> OfficeForceSaveResponse:
    try:
        await service.force_save(owner_user_id=current_user.user_id, session_id=session_id)
        return OfficeForceSaveResponse()
    except ManagedOfficeError as exc:
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/result")
async def download_result(
    session_id: str,
    revision: Annotated[int, Query(ge=1)],
    current_user: CurrentUser = Depends(get_current_user),
    service: ManagedOfficeService = Depends(get_office_service),
) -> StreamingResponse:
    try:
        record, stream = await service.result_stream(
            owner_user_id=current_user.user_id,
            session_id=session_id,
            revision=revision,
        )
    except ManagedOfficeError as exc:
        raise _http_error(exc) from exc
    return StreamingResponse(
        stream,
        media_type=_media_type(record.extension),
        headers={
            "Content-Disposition": f'attachment; filename="edited.{record.extension}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-PuppyOne-Office-Revision": str(record.result_revision),
            "X-PuppyOne-Office-SHA256": record.result_sha256,
        },
    )


@router.delete("/sessions/{session_id}", response_model=OfficeCloseResponse)
async def close_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: ManagedOfficeService = Depends(get_office_service),
) -> OfficeCloseResponse:
    try:
        await service.close(owner_user_id=current_user.user_id, session_id=session_id)
        return OfficeCloseResponse()
    except ManagedOfficeError as exc:
        raise _http_error(exc) from exc


@router.get("/engine/sessions/{session_id}/source/{filename}")
async def engine_source(
    session_id: str,
    filename: str,
    capability: Annotated[str, Query(min_length=32, max_length=1024)],
    service: ManagedOfficeService = Depends(get_office_service),
) -> StreamingResponse:
    try:
        record = await service.get_source(session_id=session_id, capability=capability)
        if filename != record.title:
            raise ManagedOfficeError("Office source was not found.", status_code=404)
    except ManagedOfficeError as exc:
        raise _http_error(exc) from exc
    return StreamingResponse(
        service.source_stream(record),
        media_type=_media_type(record.extension),
        headers={
            "Content-Disposition": f'inline; filename="source.{record.extension}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/engine/sessions/{session_id}/callback")
async def engine_callback(
    session_id: str,
    body: OfficeCallbackBody,
    capability: Annotated[str, Query(min_length=32, max_length=1024)],
    authorization: Annotated[str | None, Header()] = None,
    service: ManagedOfficeService = Depends(get_office_service),
) -> dict[str, int]:
    try:
        return await service.handle_callback(
            session_id=session_id,
            capability=capability,
            authorization=authorization,
            body=body,
        )
    except ManagedOfficeError as exc:
        raise _http_error(exc) from exc


async def _read_upload(file: UploadFile, service: ManagedOfficeService) -> bytes:
    limit = service.max_file_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ManagedOfficeError("Office document exceeds the managed size limit.", status_code=413)
        chunks.append(chunk)
    return b"".join(chunks)


def _http_error(error: ManagedOfficeError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": "managed_office_error", "message": str(error)},
    )


def _media_type(extension: str) -> str:
    return {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }[extension]
