from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest

from src.platform.office.capabilities import issue_capability, verify_capability
from src.platform.office.config import OfficeSettings
from src.platform.office.models import OfficeCallbackBody, OfficeSessionRecord
from src.platform.office.service import ManagedOfficeError, ManagedOfficeService

NOW_MS = 1_800_000_000_000
JWT_SECRET = "jwt-secret-that-is-longer-than-thirty-two-characters"
CAPABILITY_SECRET = "capability-secret-longer-than-thirty-two-characters"


class MemoryStore:
    def __init__(self):
        self.records: dict[str, OfficeSessionRecord] = {}

    async def create(self, record: OfficeSessionRecord, _ttl_seconds: int) -> bool:
        if record.session_id in self.records:
            return False
        self.records[record.session_id] = record
        return True

    async def get(self, session_id: str) -> OfficeSessionRecord | None:
        return self.records.get(session_id)

    async def set_status(
        self,
        session_id: str,
        status: str,
        *,
        message: str = "",
        ttl_seconds: int,
    ) -> OfficeSessionRecord | None:
        del ttl_seconds
        record = self.records.get(session_id)
        if record is None:
            return None
        record = record.with_updates(status=status, message=message, updated_at_ms=NOW_MS + 1)
        self.records[session_id] = record
        return record

    async def publish_result(
        self,
        session_id: str,
        *,
        result_sha256: str,
        ttl_seconds: int,
    ) -> OfficeSessionRecord | None:
        del ttl_seconds
        record = self.records.get(session_id)
        if record is None:
            return None
        revision = record.result_revision + (record.result_sha256 != result_sha256)
        record = record.with_updates(
            result_revision=revision,
            result_sha256=result_sha256,
            status="saved",
            message="",
            updated_at_ms=NOW_MS + 2,
        )
        self.records[session_id] = record
        return record

    async def count_for_owner(self, owner_user_id: str) -> int:
        return sum(record.owner_user_id == owner_user_id for record in self.records.values())

    async def delete(self, session_id: str) -> OfficeSessionRecord | None:
        return self.records.pop(session_id, None)


class MemoryBinaries:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}

    async def upload_file(self, key, content, _content_type=None, metadata=None):
        self.objects[key] = bytes(content)
        self.metadata[key] = metadata or {}

    async def download_file(self, key):
        return self.objects[key]

    async def download_file_stream(self, key, chunk_size=8192) -> AsyncIterator[bytes]:
        content = self.objects[key]
        for offset in range(0, len(content), chunk_size):
            yield content[offset : offset + chunk_size]

    async def delete_file(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        self.objects.pop(key)


def make_config(**overrides) -> OfficeSettings:
    values = {
        "PUPPYONE_OFFICE_ENABLED": True,
        "PUPPYONE_OFFICE_DOCUMENT_SERVER_URL": "https://office.puppyone.test",
        "PUPPYONE_OFFICE_PUBLIC_BASE_URL": "https://api.puppyone.test",
        "PUPPYONE_OFFICE_JWT_SECRET": JWT_SECRET,
        "PUPPYONE_OFFICE_CAPABILITY_SECRET": CAPABILITY_SECRET,
        "PUPPYONE_OFFICE_REDIS_URL": "redis://office.test/0",
        "PUPPYONE_OFFICE_DOWNLOAD_ORIGINS": "https://office.puppyone.test",
    }
    values.update(overrides)
    return OfficeSettings(_env_file=None, **values)


def make_service(*, transport=None, config=None):
    store = MemoryStore()
    binaries = MemoryBinaries()
    client = httpx.AsyncClient(
        transport=transport or httpx.MockTransport(lambda _request: httpx.Response(500)),
        follow_redirects=False,
    )
    service = ManagedOfficeService(
        config=config or make_config(),
        sessions=store,
        binaries=binaries,
        http_client=client,
        now_ms=lambda: NOW_MS,
    )
    return service, store, binaries, client


@pytest.mark.asyncio
async def test_create_session_keeps_engine_secrets_server_side_and_source_private():
    service, store, binaries, client = make_service()
    try:
        created = await service.create_session(
            owner_user_id="user-1",
            filename="contract.docx",
            content=b"docx-bytes",
            locale="zh-CN",
            display_name="Owner",
        )
    finally:
        await client.aclose()

    record = store.records[created.session_id]
    assert binaries.objects[record.source_key] == b"docx-bytes"
    assert record.source_key.startswith("office-sessions/")
    assert "user-1" not in record.source_key
    assert created.api_script_url == "https://office.puppyone.test/web-apps/apps/api/documents/api.js"
    assert created.editor_config["document"]["url"].startswith(
        f"https://api.puppyone.test/api/v1/office/engine/sessions/{created.session_id}/source/"
    )
    assert JWT_SECRET not in str(created.model_dump())
    assert CAPABILITY_SECRET not in str(created.model_dump())


@pytest.mark.asyncio
async def test_callback_rejects_body_that_does_not_match_signed_token_without_fetching():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"edited")

    service, store, _binaries, client = make_service(transport=httpx.MockTransport(handler))
    try:
        created = await service.create_session(
            owner_user_id="user-1",
            filename="sheet.xlsx",
            content=b"xlsx-source",
            locale="en",
            display_name="Owner",
        )
        record = store.records[created.session_id]
        capability = _callback_capability(created.editor_config)
        signed = jwt.encode(
            {
                "key": record.document_key,
                "status": 6,
                "url": "https://office.puppyone.test/result.xlsx",
                "filetype": "xlsx",
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(ManagedOfficeError, match="does not match"):
            await service.handle_callback(
                session_id=record.session_id,
                capability=capability,
                authorization=f"Bearer {signed}",
                body=OfficeCallbackBody(
                    key=record.document_key,
                    status=6,
                    url="https://office.puppyone.test/different.xlsx",
                    filetype="xlsx",
                ),
            )
    finally:
        await client.aclose()

    assert requests == []
    assert store.records[created.session_id].result_revision == 0


@pytest.mark.asyncio
async def test_callback_publishes_monotonic_result_and_owner_isolation():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://office.puppyone.test/result.docx"
        return httpx.Response(200, content=b"edited-docx", headers={"Content-Length": "11"})

    service, store, binaries, client = make_service(transport=httpx.MockTransport(handler))
    try:
        created = await service.create_session(
            owner_user_id="owner",
            filename="contract.docx",
            content=b"source",
            locale="en",
            display_name="Owner",
        )
        record = store.records[created.session_id]
        body = OfficeCallbackBody(
            key=record.document_key,
            status=6,
            url="https://office.puppyone.test/result.docx",
            filetype="docx",
        )
        signed = jwt.encode(body.model_dump(exclude_none=True), JWT_SECRET, algorithm="HS256")
        await service.handle_callback(
            session_id=record.session_id,
            capability=_callback_capability(created.editor_config),
            authorization=f"Bearer {signed}",
            body=body,
        )
        state = await service.get_state(owner_user_id="owner", session_id=record.session_id)
        assert state.status == "saved"
        assert state.result_revision == 1
        assert binaries.objects[record.result_key] == b"edited-docx"
        with pytest.raises(ManagedOfficeError) as denied:
            await service.get_state(owner_user_id="other", session_id=record.session_id)
        assert denied.value.status_code == 404
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_result_redirect_must_remain_on_exact_allowlist():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "https://attacker.test/stolen.docx"})

    service, store, _binaries, client = make_service(transport=httpx.MockTransport(handler))
    try:
        created = await service.create_session(
            owner_user_id="owner",
            filename="contract.docx",
            content=b"source",
            locale="en",
            display_name="Owner",
        )
        record = store.records[created.session_id]
        body = OfficeCallbackBody(
            key=record.document_key,
            status=6,
            url="https://office.puppyone.test/result.docx",
            filetype="docx",
        )
        signed = jwt.encode(body.model_dump(exclude_none=True), JWT_SECRET, algorithm="HS256")
        with pytest.raises(ManagedOfficeError, match="origin is not allowed"):
            await service.handle_callback(
                session_id=record.session_id,
                capability=_callback_capability(created.editor_config),
                authorization=f"Bearer {signed}",
                body=body,
            )
        assert store.records[record.session_id].status == "error"
    finally:
        await client.aclose()
    assert calls == 1


@pytest.mark.asyncio
async def test_close_deletes_session_and_ephemeral_objects():
    service, store, binaries, client = make_service()
    try:
        created = await service.create_session(
            owner_user_id="owner",
            filename="slides.pptx",
            content=b"source",
            locale="en",
            display_name="Owner",
        )
        record = store.records[created.session_id]
        binaries.objects[record.result_key] = b"result"
        await service.close(owner_user_id="owner", session_id=record.session_id)
    finally:
        await client.aclose()
    assert record.session_id not in store.records
    assert record.source_key not in binaries.objects
    assert record.result_key not in binaries.objects


def test_capability_is_purpose_bound_and_expires():
    token = issue_capability(
        session_id="8a7f17dc-55f4-4324-a0df-f7c10120dbcc",
        purpose="source",
        secret=CAPABILITY_SECRET,
        ttl_seconds=60,
        now_seconds=100,
    )
    assert verify_capability(
        token,
        session_id="8a7f17dc-55f4-4324-a0df-f7c10120dbcc",
        purpose="source",
        secret=CAPABILITY_SECRET,
        now_seconds=160,
    )
    assert not verify_capability(
        token,
        session_id="8a7f17dc-55f4-4324-a0df-f7c10120dbcc",
        purpose="callback",
        secret=CAPABILITY_SECRET,
        now_seconds=160,
    )
    assert not verify_capability(
        token,
        session_id="8a7f17dc-55f4-4324-a0df-f7c10120dbcc",
        purpose="source",
        secret=CAPABILITY_SECRET,
        now_seconds=161,
    )


def test_enabled_config_rejects_shared_secrets():
    with pytest.raises(ValueError, match="must be distinct"):
        make_config(PUPPYONE_OFFICE_CAPABILITY_SECRET=JWT_SECRET)


def _callback_capability(editor_config: dict) -> str:
    callback_url = editor_config["editorConfig"]["callbackUrl"]
    return parse_qs(urlsplit(callback_url).query)["capability"][0]

