"""The completions endpoint speaks the model SDK protocol, including failures."""

import re
from uuid import uuid4

import httpx
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from loguru import logger
from starlette.exceptions import HTTPException

from src.utils.request_context import request_id_var

ERRORS = {
    400: ("ai_request_invalid", "The Agent request is invalid."),
    401: ("ai_authentication_required", "Sign in to use PuppyOne compute."),
    403: ("ai_access_denied", "This account cannot use PuppyOne compute."),
    404: ("ai_unavailable", "PuppyOne compute is not enabled on this server."),
    413: ("ai_request_too_large", "This conversation is too large. Start a new conversation."),
    422: ("ai_request_invalid", "The Agent request format is incompatible with the server."),
    429: ("ai_rate_limited", "Too many Agent requests. Try again shortly."),
}


def protocol_error(status, *, headers=None):
    code, message = ERRORS.get(status, ("ai_unavailable", "PuppyOne compute is unavailable."))
    candidate = request_id_var.get() or ""
    request_id = candidate if re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", candidate) else uuid4().hex
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "type": "invalid_request_error" if status < 500 else "server_error",
                "code": code,
                "message": f"{message} Reference: {request_id}",
                "request_id": request_id,
            }
        },
        headers={**(headers or {}), "X-Request-Id": request_id, "Cache-Control": "no-store"},
    )


def log_validation_failure(error):
    # Never log input, error messages, arbitrary extra keys, or prompt contents.
    # Only schema-owned names and error categories are useful for compatibility.
    fields = {
        "model",
        "messages",
        "role",
        "content",
        "type",
        "text",
        "tool_calls",
        "tool_call_id",
        "name",
        "function",
        "arguments",
        "tools",
        "parameters",
        "reasoning_content",
        "reasoning",
        "reasoning_text",
        "reasoning_details",
        "summary",
        "data",
        "signature",
        "format",
        "index",
        "id",
        "stream",
        "stream_options",
        "include_usage",
        "tool_choice",
        "parallel_tool_calls",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "strict",
    }
    issues = [
        {
            "type": item["type"],
            "path": [
                part if isinstance(part, int) or part in fields else "<field>"
                for part in item["loc"]
            ],
        }
        for item in error.errors(include_input=False, include_context=False, include_url=False)[:8]
    ]
    logger.bind(validation_issues=issues).warning("managed_ai_request_rejected")


class InferenceRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except HTTPException as error:
                # Auth and feature-gate dependencies run before the endpoint.
                return protocol_error(error.status_code, headers=error.headers)
            except RequestValidationError:
                return protocol_error(422)
            except httpx.RequestError:
                logger.warning("managed_ai_transport_failed")
                return protocol_error(503)
            except Exception as error:
                logger.bind(exception_type=type(error).__name__).error("managed_ai_request_failed")
                return protocol_error(500)

        return handle
