from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config import settings


class OfficeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
        env_file_encoding="utf-8",
    )

    PUPPYONE_OFFICE_ENABLED: bool = False
    PUPPYONE_OFFICE_DOCUMENT_SERVER_URL: str = ""
    PUPPYONE_OFFICE_PUBLIC_BASE_URL: str = ""
    PUPPYONE_OFFICE_JWT_SECRET: str = ""
    PUPPYONE_OFFICE_CAPABILITY_SECRET: str = ""
    PUPPYONE_OFFICE_REDIS_URL: str = ""
    PUPPYONE_OFFICE_DOWNLOAD_ORIGINS: str = ""
    PUPPYONE_OFFICE_STORAGE_PREFIX: str = "office-sessions"
    PUPPYONE_OFFICE_SESSION_TTL_SECONDS: int = 30 * 60
    PUPPYONE_OFFICE_CAPABILITY_TTL_SECONDS: int = 35 * 60
    PUPPYONE_OFFICE_MAX_FILE_BYTES: int = 100 * 1024 * 1024
    PUPPYONE_OFFICE_MAX_SESSIONS_PER_USER: int = 4
    PUPPYONE_OFFICE_HTTP_TIMEOUT_SECONDS: float = 35.0

    @field_validator(
        "PUPPYONE_OFFICE_DOCUMENT_SERVER_URL",
        "PUPPYONE_OFFICE_PUBLIC_BASE_URL",
        mode="before",
    )
    @classmethod
    def normalize_url(cls, value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @field_validator("PUPPYONE_OFFICE_STORAGE_PREFIX", mode="before")
    @classmethod
    def normalize_prefix(cls, value: object) -> str:
        normalized = str(value or "").strip().strip("/")
        if not normalized or ".." in normalized.split("/"):
            raise ValueError("PUPPYONE_OFFICE_STORAGE_PREFIX is invalid")
        return normalized

    @model_validator(mode="after")
    def validate_enabled_service(self):
        if not self.PUPPYONE_OFFICE_ENABLED:
            return self
        if not self.PUPPYONE_OFFICE_PUBLIC_BASE_URL:
            self.PUPPYONE_OFFICE_PUBLIC_BASE_URL = settings.PUBLIC_URL.rstrip("/")
        if not self.PUPPYONE_OFFICE_REDIS_URL:
            self.PUPPYONE_OFFICE_REDIS_URL = settings.AUTH_SECURITY_REDIS_URL
        required = {
            "PUPPYONE_OFFICE_DOCUMENT_SERVER_URL": self.PUPPYONE_OFFICE_DOCUMENT_SERVER_URL,
            "PUPPYONE_OFFICE_PUBLIC_BASE_URL": self.PUPPYONE_OFFICE_PUBLIC_BASE_URL,
            "PUPPYONE_OFFICE_REDIS_URL": self.PUPPYONE_OFFICE_REDIS_URL,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Managed Office requires {', '.join(missing)}")
        for name, secret in (
            ("PUPPYONE_OFFICE_JWT_SECRET", self.PUPPYONE_OFFICE_JWT_SECRET),
            ("PUPPYONE_OFFICE_CAPABILITY_SECRET", self.PUPPYONE_OFFICE_CAPABILITY_SECRET),
        ):
            if len(secret) < 32:
                raise ValueError(f"{name} must contain at least 32 characters")
        if self.PUPPYONE_OFFICE_JWT_SECRET == self.PUPPYONE_OFFICE_CAPABILITY_SECRET:
            raise ValueError("Office JWT and capability secrets must be distinct")
        if settings.APP_ENV in {"staging", "production"}:
            for name, value in (
                ("PUPPYONE_OFFICE_DOCUMENT_SERVER_URL", self.PUPPYONE_OFFICE_DOCUMENT_SERVER_URL),
                ("PUPPYONE_OFFICE_PUBLIC_BASE_URL", self.PUPPYONE_OFFICE_PUBLIC_BASE_URL),
            ):
                if urlsplit(value).scheme != "https":
                    raise ValueError(f"{name} must use HTTPS outside development/test")
        if self.PUPPYONE_OFFICE_SESSION_TTL_SECONDS < 60:
            raise ValueError("Office session TTL must be at least 60 seconds")
        if self.PUPPYONE_OFFICE_CAPABILITY_TTL_SECONDS < self.PUPPYONE_OFFICE_SESSION_TTL_SECONDS:
            raise ValueError("Office capability TTL cannot be shorter than the session TTL")
        if not 1 <= self.PUPPYONE_OFFICE_MAX_SESSIONS_PER_USER <= 16:
            raise ValueError("Office per-user session limit must be between 1 and 16")
        if not 1 <= self.PUPPYONE_OFFICE_MAX_FILE_BYTES <= 250 * 1024 * 1024:
            raise ValueError("Office file byte limit must be between 1 byte and 250 MiB")
        return self

    @property
    def document_server_origin(self) -> str:
        return _origin(self.PUPPYONE_OFFICE_DOCUMENT_SERVER_URL)

    @property
    def download_origins(self) -> frozenset[str]:
        configured = {
            _origin(value.strip())
            for value in self.PUPPYONE_OFFICE_DOWNLOAD_ORIGINS.split(",")
            if value.strip()
        }
        configured.add(self.document_server_origin)
        return frozenset(configured)


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Managed Office URL is invalid")
    if parsed.username or parsed.password:
        raise ValueError("Managed Office URL must not contain credentials")
    return f"{parsed.scheme}://{parsed.netloc}"


office_settings = OfficeSettings()

