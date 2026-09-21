"""Encode application events into the Desktop SDK's public SSE protocol."""

import json

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from src.platform.managed_ai.contracts import (
    InferenceDone,
    InferenceFailed,
    InferenceRun,
    ModelChunk,
)


def completion_response(run: InferenceRun) -> StreamingResponse:
    async def encode():
        try:
            async for event in run.events:
                if isinstance(event, ModelChunk):
                    yield f"data: {json.dumps(event.frame)}\n\n".encode()
                elif isinstance(event, InferenceDone):
                    yield b"data: [DONE]\n\n"
                elif isinstance(event, InferenceFailed):
                    payload = {"error": {"code": event.code, "message": event.message}}
                    yield f"data: {json.dumps(payload)}\n\n".encode()
                else:
                    raise TypeError("Unsupported inference event")
        finally:
            await run.aclose()

    return StreamingResponse(
        encode(),
        media_type="text/event-stream",
        background=BackgroundTask(run.aclose),
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-PuppyOne-Reservation-ID": run.reservation_id,
        },
    )
