"""stdlib logging interception keeps per-request client lines out of INFO logs."""

import logging

import pytest

from src.utils.logging_setup import _intercept_stdlib_logging

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
