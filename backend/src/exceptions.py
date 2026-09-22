from enum import Enum
from typing import Any


class ErrorCode(int, Enum):
    """Global error code definitions"""

    SUCCESS = 0

    # General errors (1000-1999)
    INTERNAL_SERVER_ERROR = 1000
    BAD_REQUEST = 1001
    UNAUTHORIZED = 1002
    FORBIDDEN = 1003
    NOT_FOUND = 1004
    METHOD_NOT_ALLOWED = 1005
    VALIDATION_ERROR = 1006
    CLIENT_UPGRADE_REQUIRED = 1007
    TARGET_KIND_MISMATCH = 1008
    SCOPE_NOT_FOUND = 1009
    REPOSITORY_STORAGE_UNAVAILABLE = 1010

    # User/authentication related (2000-2999)
    USER_NOT_FOUND = 2001
    USER_ALREADY_EXISTS = 2002
    INVALID_CREDENTIALS = 2003
    TOKEN_EXPIRED = 2004
    INVALID_TOKEN = 2005

    # Content node related (4000-4999)
    NAME_CONFLICT = 4001  # Duplicate name exists in the same directory
    VERSION_CONFLICT = 4002  # Optimistic lock version conflict (concurrent write)
    CAS_RETRY_EXHAUSTED = 4003

    # MCP related (3000-3999)
    MCP_INSTANCE_NOT_FOUND = 3001
    MCP_INSTANCE_CREATION_FAILED = 3002
    MCP_INSTANCE_UPDATE_FAILED = 3003
    MCP_INSTANCE_DELETE_FAILED = 3004
    MCP_SERVER_ERROR = 3005


class AppException(Exception):
    """Application base exception class"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = 400,
        details: Any | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(self.message)


# Concrete exception class helpers
class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found", code: ErrorCode = ErrorCode.NOT_FOUND):
        super().__init__(code=code, message=message, status_code=404)


class ValidationException(AppException):
    def __init__(self, message: str = "Validation error", details: Any = None):
        super().__init__(
            code=ErrorCode.VALIDATION_ERROR,
            message=message,
            status_code=422,
            details=details,
        )


class AuthException(AppException):
    def __init__(
        self,
        message: str = "Authentication failed",
        code: ErrorCode = ErrorCode.UNAUTHORIZED,
    ):
        super().__init__(code=code, message=message, status_code=401)


class PermissionException(AppException):
    def __init__(self, message: str = "Permission denied", code: ErrorCode = ErrorCode.FORBIDDEN):
        super().__init__(code=code, message=message, status_code=403)


class ServiceUnavailableException(AppException):
    """A fail-closed dependency outage that callers may safely retry."""

    def __init__(
        self,
        message: str = "Service temporarily unavailable",
        *,
        retry_after_seconds: int = 1,
    ):
        self.headers = {"Retry-After": str(max(1, retry_after_seconds))}
        super().__init__(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            message=message,
            status_code=503,
            details={"retryable": True},
        )


class DatabaseSchemaOutdatedException(AppException):
    """The deployed API requires a database migration not visible to PostgREST.

    This is deliberately safe and machine-readable: callers can distinguish a
    rolling-deploy mismatch from an ordinary credential failure without ever
    receiving the missing function name, SQL signature, or schema-cache body.
    """

    def __init__(self, *, retry_after_seconds: int = 30):
        self.headers = {"Retry-After": str(max(1, retry_after_seconds))}
        super().__init__(
            code=ErrorCode.REPOSITORY_STORAGE_UNAVAILABLE,
            message="Cloud service upgrade is still being applied; try again shortly",
            status_code=503,
            details={
                "code": "database_schema_outdated",
                "retryable": True,
            },
        )


# Alias for HTTP 403 Forbidden (used by organization service)
ForbiddenException = PermissionException


class BusinessException(AppException):
    """Business logic error"""

    def __init__(self, message: str, code: ErrorCode = ErrorCode.BAD_REQUEST):
        super().__init__(code=code, message=message, status_code=400)


class NameConflictException(AppException):
    """Duplicate name exists in the same directory"""

    def __init__(self, message: str = "A node with this name already exists in the folder"):
        super().__init__(
            code=ErrorCode.NAME_CONFLICT,
            message=message,
            status_code=409,
        )


class VersionConflictException(AppException):
    """Version conflict: optimistic lock failure due to concurrent write"""

    def __init__(self, message: str = "Version conflict: concurrent update detected"):
        super().__init__(
            code=ErrorCode.VERSION_CONFLICT,
            message=message,
            status_code=409,
        )


class CasRetriesExhausted(AppException):
    """A write lost every bounded CAS attempt and is safe to retry later."""

    def __init__(self, message: str = "Concurrent write contention; retry the request"):
        self.headers = {"Retry-After": "1"}
        super().__init__(
            code=ErrorCode.CAS_RETRY_EXHAUSTED,
            message=message,
            status_code=409,
            details={"retryable": True},
        )
