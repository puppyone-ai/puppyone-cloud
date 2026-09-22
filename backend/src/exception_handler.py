from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.common_schemas import ApiResponse
from src.exceptions import AppException, ErrorCode
from src.platform.auth.shared_security_store import SecurityStoreUnavailable
from src.utils.request_context import request_id_var


def _http_error_code(status_code: int) -> ErrorCode:
    if status_code == 401:
        return ErrorCode.UNAUTHORIZED
    if status_code == 403:
        return ErrorCode.FORBIDDEN
    if status_code == 404:
        return ErrorCode.NOT_FOUND
    if status_code == 409:
        return ErrorCode.VERSION_CONFLICT
    if status_code == 422:
        return ErrorCode.VALIDATION_ERROR
    if status_code >= 500:
        return ErrorCode.INTERNAL_SERVER_ERROR
    return ErrorCode.BAD_REQUEST


def app_exception_handler(request: Request, exc: AppException):
    """Handle custom application exceptions"""
    # Note: AppException (4xx/business errors) should also be logged by default for troubleshooting.
    # Previously only returning the response without logging created the illusion of "no errors visible".
    rid = request_id_var.get()
    log = logger.bind(
        err_code=int(exc.code),
        err_status=exc.status_code,
        err_message=exc.message,
        request_id=rid,
    )
    if exc.status_code >= 500:
        log.error("AppException")
    else:
        log.warning("AppException")

    resp = JSONResponse(
        status_code=exc.status_code,
        content=ApiResponse.error(
            code=exc.code, message=exc.message, data=exc.details
        ).model_dump(),
    )
    if rid:
        resp.headers["X-Request-Id"] = rid
    for key, value in getattr(exc, "headers", {}).items():
        resp.headers[key] = value
    return resp


def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle FastAPI/Starlette HTTPException"""
    detail = exc.detail
    data = None
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("detail") or "Request failed")
        data = detail
    else:
        message = str(detail)

    resp = JSONResponse(
        status_code=exc.status_code,
        content=ApiResponse.error(
            code=_http_error_code(exc.status_code),
            message=message,
            data=data,
        ).model_dump(),
        # Starlette's default handler preserves HTTPException headers. Keep
        # that contract when wrapping the body in ApiResponse: Git smart HTTP
        # depends on WWW-Authenticate to trigger Basic credential lookup/retry,
        # and other protocol responses may rely on Allow or Retry-After.
        headers=exc.headers,
    )
    rid = request_id_var.get()
    if rid:
        resp.headers["X-Request-Id"] = rid
    return resp


def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle request parameter validation exceptions"""
    # Convert Pydantic's error list to a more readable format
    errors = []
    for error in exc.errors():
        loc = ".".join([str(x) for x in error["loc"]])
        msg = error["msg"]
        errors.append(f"{loc}: {msg}")

    resp = JSONResponse(
        status_code=422,
        content=ApiResponse.error(
            code=ErrorCode.VALIDATION_ERROR, message="Validation Error", data=errors
        ).model_dump(),
    )
    rid = request_id_var.get()
    if rid:
        resp.headers["X-Request-Id"] = rid
    return resp


def security_store_unavailable_handler(
    request: Request,
    exc: SecurityStoreUnavailable,
):
    """Expose fail-closed authentication infrastructure failures as HTTP 503."""
    rid = request_id_var.get()
    logger.bind(request_id=rid).error(
        "Authentication security store unavailable: {}",
        exc,
    )
    resp = JSONResponse(
        status_code=503,
        content=ApiResponse.error(
            code=ErrorCode.INTERNAL_SERVER_ERROR,
            message="Authentication security store unavailable",
        ).model_dump(),
    )
    if rid:
        resp.headers["X-Request-Id"] = rid
    return resp


def generic_exception_handler(request: Request, exc: Exception):
    """Handle all uncaught exceptions"""
    # Log full stack trace; request context (request_id/path/method etc.) injected by middleware + patcher
    logger.exception("Unhandled exception")
    resp = JSONResponse(
        status_code=500,
        content=ApiResponse.error(
            code=ErrorCode.INTERNAL_SERVER_ERROR, message="Internal Server Error"
        ).model_dump(),
    )
    rid = request_id_var.get()
    if rid:
        resp.headers["X-Request-Id"] = rid
    return resp
