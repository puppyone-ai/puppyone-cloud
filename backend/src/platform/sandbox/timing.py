"""Safe request-correlated workspace timings; never log commands or credentials."""

import logging
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def workspace_event(stage, **fields):
    logger.info("workspace_stage", extra={"workspace_performance": {"stage": stage, **fields}})


@contextmanager
def workspace_stage(stage, **fields):
    started = time.monotonic()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "failed"
        raise
    finally:
        workspace_event(
            stage,
            elapsed_ms=round((time.monotonic() - started) * 1000, 2),
            outcome=outcome,
            **fields,
        )
