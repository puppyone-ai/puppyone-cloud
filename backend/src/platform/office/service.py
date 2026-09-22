from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import PurePath
from typing import Protocol
from urllib.parse import quote, urljoin, urlsplit

import httpx
import jwt

from src.platform.office.capabilities import issue_capability, verify_capability
from src.platform.office.config import OfficeSettings
from src.platform.office.models import (
    OfficeAvailabilityResponse,
    OfficeCallbackBody,
    OfficeEditorSessionResponse,
    OfficeSessionRecord,
    OfficeSessionStateResponse,
    iso_from_millis,
)
from src.platform.office.store import OfficeSessionStore

_SESSION_ID = re.compile(r"^[0-9a-f-]{36}$", re.IGNORECASE)
_SUPPORTED_EXTENSIONS = {"docx": "word", "xlsx": "cell", "pptx": "slide"}
_MIME_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class OfficeBinaryStore(Protocol):
    async def upload_file(
        self,
        key: str,
        content: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ): ...
    async def download_file(self, key: str) -> bytes: ...
    def download_file_stream(self, key: str, chunk_size: int = 8192) -> AsyncIterator[bytes]: ...
    async def delete_file(self, key: str) -> None: ...


class ManagedOfficeError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class ManagedOfficeService:
    def __init__(
        self,
        *,
        config: OfficeSettings,
        sessions: OfficeSessionStore,
        binaries: OfficeBinaryStore,
        http_client: httpx.AsyncClient | None = None,
        now_ms=lambda: int(time.time() * 1000),
    ):
        self._config = config
        self._sessions = sessions
        self._binaries = binaries
        self._http = http_client
        self._owns_http = http_client is None
        self._now_ms = now_ms

    def availability(self) -> OfficeAvailabilityResponse:
        if not self._config.PUPPYONE_OFFICE_ENABLED:
            return OfficeAvailabilityResponse(
                available=False,
                reason="Managed Office editing is not enabled for this environment.",
            )
        return OfficeAvailabilityResponse(available=True)

    @property
    def max_file_bytes(self) -> int:
        return self._config.PUPPYONE_OFFICE_MAX_FILE_BYTES

    async def create_session(
        self,
        *,
        owner_user_id: str,
        filename: str,
        content: bytes,
        locale: str,
        display_name: str,
    ) -> OfficeEditorSessionResponse:
        self._require_enabled()
        title, extension = _validate_document(filename)
        if not content:
            raise ManagedOfficeError("Office document is empty.")
        if len(content) > self._config.PUPPYONE_OFFICE_MAX_FILE_BYTES:
            raise ManagedOfficeError("Office document exceeds the managed size limit.", status_code=413)
        count = await self._sessions.count_for_owner(owner_user_id)
        if count >= self._config.PUPPYONE_OFFICE_MAX_SESSIONS_PER_USER:
            raise ManagedOfficeError("Too many Office documents are already open.", status_code=429)

        session_id = str(uuid.uuid4())
        now = self._now_ms()
        ttl_seconds = self._config.PUPPYONE_OFFICE_SESSION_TTL_SECONDS
        expires_at = now + ttl_seconds * 1000
        owner_hash = hashlib.sha256(owner_user_id.encode()).hexdigest()[:24]
        prefix = self._config.PUPPYONE_OFFICE_STORAGE_PREFIX
        source_key = f"{prefix}/{owner_hash}/{session_id}/source.{extension}"
        result_key = f"{prefix}/{owner_hash}/{session_id}/result.{extension}"
        document_key = hashlib.sha256(f"{session_id}\0{hashlib.sha256(content).hexdigest()}".encode()).hexdigest()
        record = OfficeSessionRecord(
            session_id=session_id,
            owner_user_id=owner_user_id,
            document_key=document_key,
            title=title,
            extension=extension,
            source_key=source_key,
            result_key=result_key,
            status="ready",
            result_revision=0,
            result_sha256="",
            message="",
            created_at_ms=now,
            updated_at_ms=now,
            expires_at_ms=expires_at,
        )
        await self._binaries.upload_file(
            source_key,
            content,
            _MIME_TYPES[extension],
            {
                "ephemeral": "true",
                "expires-at-ms": str(expires_at),
                "office-session": session_id,
            },
        )
        try:
            if not await self._sessions.create(record, ttl_seconds):
                raise ManagedOfficeError("Unable to reserve managed Office session.", status_code=503)
        except Exception:
            await self._delete_object_best_effort(source_key)
            raise

        source_token = issue_capability(
            session_id=session_id,
            purpose="source",
            secret=self._config.PUPPYONE_OFFICE_CAPABILITY_SECRET,
            ttl_seconds=self._config.PUPPYONE_OFFICE_CAPABILITY_TTL_SECONDS,
            now_seconds=now // 1000,
        )
        callback_token = issue_capability(
            session_id=session_id,
            purpose="callback",
            secret=self._config.PUPPYONE_OFFICE_CAPABILITY_SECRET,
            ttl_seconds=self._config.PUPPYONE_OFFICE_CAPABILITY_TTL_SECONDS,
            now_seconds=now // 1000,
        )
        public = self._config.PUPPYONE_OFFICE_PUBLIC_BASE_URL
        source_url = (
            f"{public}/api/v1/office/engine/sessions/{session_id}/source/"
            f"{quote(title)}?capability={quote(source_token)}"
        )
        callback_url = (
            f"{public}/api/v1/office/engine/sessions/{session_id}/callback"
            f"?capability={quote(callback_token)}"
        )
        unsigned_config = {
            "document": {
                "fileType": extension,
                "key": document_key,
                "title": title,
                "url": source_url,
                "permissions": {
                    "edit": True,
                    "download": True,
                    "print": True,
                    "review": True,
                },
            },
            "documentType": _SUPPORTED_EXTENSIONS[extension],
            "editorConfig": {
                "mode": "edit",
                "callbackUrl": callback_url,
                "lang": _normalize_locale(locale),
                "customization": {"autosave": True, "forcesave": True},
                "user": {
                    "id": f"puppyone-{owner_hash}",
                    "name": (display_name or "PuppyOne user")[:128],
                },
            },
            "height": "100%",
            "type": "desktop",
            "width": "100%",
        }
        editor_config = {
            **unsigned_config,
            "token": jwt.encode(
                {**unsigned_config, "exp": expires_at // 1000},
                self._config.PUPPYONE_OFFICE_JWT_SECRET,
                algorithm="HS256",
            ),
        }
        return OfficeEditorSessionResponse(
            session_id=session_id,
            api_script_url=(
                f"{self._config.PUPPYONE_OFFICE_DOCUMENT_SERVER_URL}"
                "/web-apps/apps/api/documents/api.js"
            ),
            editor_config=editor_config,
            status=record.status,
            result_revision=0,
            expires_at=iso_from_millis(expires_at),
        )

    async def get_state(self, *, owner_user_id: str, session_id: str) -> OfficeSessionStateResponse:
        record = await self._get_owned(owner_user_id, session_id)
        return _state_response(record)

    async def get_source(self, *, session_id: str, capability: str) -> OfficeSessionRecord:
        self._verify_capability(session_id, capability, "source")
        record = await self._get_active(session_id)
        return record

    def source_stream(self, record: OfficeSessionRecord) -> AsyncIterator[bytes]:
        return self._binaries.download_file_stream(record.source_key, 64 * 1024)

    async def handle_callback(
        self,
        *,
        session_id: str,
        capability: str,
        authorization: str | None,
        body: OfficeCallbackBody,
    ) -> dict[str, int]:
        self._verify_capability(session_id, capability, "callback")
        record = await self._get_active(session_id)
        self._verify_callback_jwt(authorization, body, record)
        if body.key != record.document_key:
            raise ManagedOfficeError("Office callback document key is invalid.", status_code=401)
        status = body.status
        if status == 1:
            await self._sessions.set_status(
                session_id,
                "editing",
                ttl_seconds=self._config.PUPPYONE_OFFICE_SESSION_TTL_SECONDS,
            )
            return {"error": 0}
        if status == 4:
            await self._sessions.set_status(
                session_id,
                "closed",
                ttl_seconds=self._config.PUPPYONE_OFFICE_SESSION_TTL_SECONDS,
            )
            return {"error": 0}
        if status in {3, 7}:
            await self._sessions.set_status(
                session_id,
                "error",
                message=f"ONLYOFFICE reported save error status {status}.",
                ttl_seconds=self._config.PUPPYONE_OFFICE_SESSION_TTL_SECONDS,
            )
            return {"error": 0}
        if status not in {2, 6}:
            return {"error": 0}
        if not body.url:
            raise ManagedOfficeError("Office save callback did not include a result URL.")
        if (body.filetype or "").lower() != record.extension:
            raise ManagedOfficeError("Office result format does not match the source document.")
        await self._sessions.set_status(
            session_id,
            "saving",
            ttl_seconds=self._config.PUPPYONE_OFFICE_SESSION_TTL_SECONDS,
        )
        try:
            result = await self._download_result(body.url)
            result_hash = hashlib.sha256(result).hexdigest()
            await self._binaries.upload_file(
                record.result_key,
                result,
                _MIME_TYPES[record.extension],
                {
                    "ephemeral": "true",
                    "expires-at-ms": str(record.expires_at_ms),
                    "office-session": session_id,
                    "sha256": result_hash,
                },
            )
            updated = await self._sessions.publish_result(
                session_id,
                result_sha256=result_hash,
                ttl_seconds=self._config.PUPPYONE_OFFICE_SESSION_TTL_SECONDS,
            )
            if updated is None:
                await self._delete_object_best_effort(record.result_key)
                raise ManagedOfficeError("Office session expired while saving.", status_code=410)
        except Exception as exc:
            await self._sessions.set_status(
                session_id,
                "error",
                message=str(exc)[:500],
                ttl_seconds=self._config.PUPPYONE_OFFICE_SESSION_TTL_SECONDS,
            )
            raise
        return {"error": 0}

    async def force_save(self, *, owner_user_id: str, session_id: str) -> None:
        record = await self._get_owned(owner_user_id, session_id)
        await self._sessions.set_status(
            session_id,
            "saving",
            ttl_seconds=self._config.PUPPYONE_OFFICE_SESSION_TTL_SECONDS,
        )
        command = {"c": "forcesave", "key": record.document_key}
        payload = {
            **command,
            "token": jwt.encode(
                {**command, "exp": int(time.time()) + 120},
                self._config.PUPPYONE_OFFICE_JWT_SECRET,
                algorithm="HS256",
            ),
        }
        response = await self._client().post(
            (
                f"{self._config.PUPPYONE_OFFICE_DOCUMENT_SERVER_URL}/command"
                f"?shardkey={quote(record.document_key)}"
            ),
            json=payload,
        )
        if response.status_code >= 400:
            raise ManagedOfficeError(f"Office save command failed ({response.status_code}).", status_code=502)
        try:
            result = response.json()
        except ValueError as exc:
            raise ManagedOfficeError("Office save command returned invalid JSON.", status_code=502) from exc
        if int(result.get("error", -1)) != 0:
            raise ManagedOfficeError(
                f"Office save command was rejected ({result.get('error', 'unknown')}).",
                status_code=502,
            )

    async def result_stream(
        self,
        *,
        owner_user_id: str,
        session_id: str,
        revision: int,
    ) -> tuple[OfficeSessionRecord, AsyncIterator[bytes]]:
        record = await self._get_owned(owner_user_id, session_id)
        if revision <= 0 or revision != record.result_revision or not record.result_sha256:
            raise ManagedOfficeError("Office result revision is unavailable.", status_code=404)
        return record, self._binaries.download_file_stream(record.result_key, 64 * 1024)

    async def close(self, *, owner_user_id: str, session_id: str) -> None:
        record = await self._get_owned(owner_user_id, session_id)
        deleted = await self._sessions.delete(session_id)
        if deleted is None:
            return
        await self._delete_object_best_effort(record.source_key)
        await self._delete_object_best_effort(record.result_key)

    async def close_http(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    async def _get_owned(self, owner_user_id: str, session_id: str) -> OfficeSessionRecord:
        record = await self._get_active(session_id)
        if record.owner_user_id != owner_user_id:
            raise ManagedOfficeError("Office session was not found.", status_code=404)
        return record

    async def _get_active(self, session_id: str) -> OfficeSessionRecord:
        if not _SESSION_ID.fullmatch(session_id):
            raise ManagedOfficeError("Office session was not found.", status_code=404)
        record = await self._sessions.get(session_id)
        if record is None or record.expires_at_ms < self._now_ms():
            raise ManagedOfficeError("Office session was not found.", status_code=404)
        return record

    def _verify_capability(self, session_id: str, capability: str, purpose: str) -> None:
        if not verify_capability(
            capability,
            session_id=session_id,
            purpose=purpose,
            secret=self._config.PUPPYONE_OFFICE_CAPABILITY_SECRET,
        ):
            raise ManagedOfficeError("Office capability is invalid or expired.", status_code=401)

    def _verify_callback_jwt(
        self,
        authorization: str | None,
        body: OfficeCallbackBody,
        record: OfficeSessionRecord,
    ) -> None:
        match = re.fullmatch(r"Bearer\s+(.+)", authorization or "", flags=re.IGNORECASE)
        if not match:
            raise ManagedOfficeError("Office callback authorization is required.", status_code=401)
        try:
            verified = jwt.decode(
                match.group(1),
                self._config.PUPPYONE_OFFICE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            raise ManagedOfficeError("Office callback authorization is invalid.", status_code=401) from exc
        signed = verified.get("payload") if isinstance(verified.get("payload"), dict) else verified
        comparisons = (
            signed.get("key") == record.document_key == body.key,
            int(signed.get("status", -1)) == body.status,
            body.url is None or signed.get("url") == body.url,
            body.filetype is None or signed.get("filetype") == body.filetype,
        )
        if not all(comparisons):
            raise ManagedOfficeError("Office callback token does not match its body.", status_code=401)

    async def _download_result(self, raw_url: str) -> bytes:
        url = self._require_allowed_result_url(raw_url)
        for _ in range(5):
            async with self._client().stream("GET", url, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ManagedOfficeError("Office result redirect has no location.", status_code=502)
                    url = self._require_allowed_result_url(urljoin(url, location))
                    continue
                if response.status_code >= 400:
                    raise ManagedOfficeError(
                        f"Office result download failed ({response.status_code}).",
                        status_code=502,
                    )
                declared = int(response.headers.get("content-length") or 0)
                if declared > self._config.PUPPYONE_OFFICE_MAX_FILE_BYTES:
                    raise ManagedOfficeError("Office result exceeds the managed size limit.", status_code=413)
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > self._config.PUPPYONE_OFFICE_MAX_FILE_BYTES:
                        raise ManagedOfficeError("Office result exceeds the managed size limit.", status_code=413)
                    chunks.append(chunk)
                return b"".join(chunks)
        raise ManagedOfficeError("Office result used too many redirects.", status_code=502)

    def _require_allowed_result_url(self, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            raise ManagedOfficeError("Office result URL is invalid.", status_code=400)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme not in {"http", "https"} or origin not in self._config.download_origins:
            raise ManagedOfficeError("Office result URL origin is not allowed.", status_code=400)
        return value

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self._config.PUPPYONE_OFFICE_HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            )
        return self._http

    async def _delete_object_best_effort(self, key: str) -> None:
        try:
            await self._binaries.delete_file(key)
        except Exception:
            return

    def _require_enabled(self) -> None:
        if not self._config.PUPPYONE_OFFICE_ENABLED:
            raise ManagedOfficeError("Managed Office editing is unavailable.", status_code=503)


def _validate_document(filename: str) -> tuple[str, str]:
    title = PurePath(filename).name
    extension = PurePath(title).suffix.lower().lstrip(".")
    if not title or len(title) > 255 or extension not in _SUPPORTED_EXTENSIONS:
        raise ManagedOfficeError("Only DOCX, XLSX, and PPTX files can be edited.")
    return title, extension


def _normalize_locale(value: str) -> str:
    return value if re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", value or "") else "en"


def _state_response(record: OfficeSessionRecord) -> OfficeSessionStateResponse:
    return OfficeSessionStateResponse(
        session_id=record.session_id,
        status=record.status,
        result_revision=record.result_revision,
        message=record.message or None,
        expires_at=iso_from_millis(record.expires_at_ms),
    )
