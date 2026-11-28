"""stdlib logging interception keeps per-request client lines out of INFO logs."""

import logging

import pytest
from loguru import logger as loguru_logger

from src.utils.logging_setup import REPORT_FIELDS, InterceptHandler, _intercept_stdlib_logging

INTERCEPTED = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "fastapi",
    "starlette",
    "httpx",
    "asyncio",
)


@pytest.fixture(autouse=True)
def restore_logging():
    loggers = [logging.root, *(logging.getLogger(name) for name in INTERCEPTED)]
    saved = [(lg, lg.handlers[:], lg.level, lg.propagate, lg.disabled) for lg in loggers]
    yield
    for lg, handlers, level, propagate, disabled in saved:
        lg.handlers, lg.propagate, lg.disabled = handlers, propagate, disabled
        lg.setLevel(level)


@pytest.mark.parametrize(
    ("level", "request_lines"), [("INFO", False), ("WARNING", False), ("DEBUG", True)]
)
def test_httpx_request_lines_are_debug_only(level, request_lines):
    _intercept_stdlib_logging(level, disable_uvicorn_access=True)

    httpx_logger = logging.getLogger("httpx")
    assert httpx_logger.isEnabledFor(logging.INFO) is request_lines
    assert httpx_logger.isEnabledFor(logging.WARNING)


def test_intercept_handler_preserves_workbench_open_and_drops_unlisted_extras():
    assert "workbench_open" in REPORT_FIELDS
    captured = []
    sink_id = loguru_logger.add(lambda message: captured.append(message.record))
    target_logger = logging.getLogger("tests.infra.intercept_probe")
    saved = (target_logger.handlers[:], target_logger.level, target_logger.propagate)
    try:
        target_logger.handlers = [InterceptHandler()]
        target_logger.setLevel(logging.INFO)
        target_logger.propagate = False
        report = {
            "stage": "open",
            "project_id": "proj-1",
            "code": "workbench_connection_unavailable",
            "status": 503,
            "retryable": True,
            "error_type": "WorkspaceUnavailable",
            "request_id": "req-123",
        }
        target_logger.warning(
            "workbench_open_failed",
            extra={
                "workbench_open": report,
                "unlisted_secret": "must-not-leak",
            },
        )
    finally:
        target_logger.handlers, target_logger.propagate = saved[0], saved[2]
        target_logger.setLevel(saved[1])
        loguru_logger.remove(sink_id)

    assert len(captured) == 1
    assert captured[0]["message"] == "workbench_open_failed"
    assert captured[0]["extra"].get("workbench_open") == report
    assert "unlisted_secret" not in captured[0]["extra"]
