from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.office.dependencies import get_office_service
from src.platform.office.models import (
    OfficeAvailabilityResponse,
    OfficeEditorSessionResponse,
    OfficeSessionRecord,
    OfficeSessionStateResponse,
)
from src.platform.office.router import router

SESSION_ID = "11111111-1111-4111-8111-111111111111"


class FakeOfficeService:
    max_file_bytes = 12

    def __init__(self):
        self.created: dict | None = None

    def availability(self) -> OfficeAvailabilityResponse:
        return OfficeAvailabilityResponse(available=True)

    async def create_session(self, **kwargs) -> OfficeEditorSessionResponse:
        self.created = kwargs
        return OfficeEditorSessionResponse(
            session_id=SESSION_ID,
            api_script_url=(
                "https://office.puppyone.ai/web-apps/apps/api/documents/api.js"
            ),
            editor_config={"token": "server-signed"},
            status="ready",
            result_revision=0,
            expires_at="2027-01-15T08:00:00Z",
        )

    async def get_state(self, **_kwargs) -> OfficeSessionStateResponse:
        return OfficeSessionStateResponse(
            session_id=SESSION_ID,
            status="saved",
            result_revision=1,
            expires_at="2027-01-15T08:00:00Z",
        )

    async def result_stream(self, **_kwargs):
        record = OfficeSessionRecord(
            session_id=SESSION_ID,
            owner_user_id="user-1",
            document_key="key",
            title="report.docx",
            extension="docx",
            source_key="source",
            result_key="result",
            status="saved",
            result_revision=1,
            result_sha256="a" * 64,
            message="",
            created_at_ms=1,
            updated_at_ms=2,
            expires_at_ms=3,
        )
        return record, _stream(b"edited")


async def _stream(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _app(service: FakeOfficeService, *, authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_office_service] = lambda: service
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="user-1",
            email="user@example.com",
            role="authenticated",
        )
    return app


def test_create_session_is_authenticated_and_forwards_no_desktop_path() -> None:
    service = FakeOfficeService()
    with TestClient(_app(service)) as client:
        response = client.post(
            "/api/v1/office/sessions",
            files={"file": ("report.docx", b"source", "application/octet-stream")},
            data={"locale": "zh-CN"},
        )

    assert response.status_code == 201
    assert response.json()["api_script_url"].startswith("https://office.puppyone.ai/")
    assert service.created == {
        "owner_user_id": "user-1",
        "filename": "report.docx",
        "content": b"source",
        "locale": "zh-CN",
        "display_name": "user@example.com",
    }


def test_upload_limit_is_enforced_before_service_session_creation() -> None:
    service = FakeOfficeService()
    with TestClient(_app(service)) as client:
        response = client.post(
            "/api/v1/office/sessions",
            files={"file": ("report.docx", b"more-than-twelve", "application/octet-stream")},
        )

    assert response.status_code == 413
    assert service.created is None


def test_result_download_is_private_versioned_binary() -> None:
    service = FakeOfficeService()
    with TestClient(_app(service)) as client:
        response = client.get(
            f"/api/v1/office/sessions/{SESSION_ID}/result?revision=1"
        )

    assert response.status_code == 200
    assert response.content == b"edited"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-puppyone-office-revision"] == "1"
    assert response.headers["x-puppyone-office-sha256"] == "a" * 64
