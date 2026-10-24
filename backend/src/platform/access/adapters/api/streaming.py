"""HTTP streaming owns the reader until its last worker has finished."""

from __future__ import annotations

from starlette.responses import StreamingResponse

from src.infra.execution.runner import run_cleanup, run_owned

_END = object()


def _next(iterator):
    return next(iterator, _END)


class OwnedStreamingResponse(StreamingResponse):
    """Close snapshot/iterator even on disconnect or response startup failure."""

    def __init__(self, iterator, *, release, **kwargs):
        super().__init__(iterator, **kwargs)
        self._iterator = iter(iterator)
        self._release = release

    async def stream_response(self, send):
        await send(
            {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
        )
        while True:
            chunk = await run_owned(_next, self._iterator)
            if chunk is _END:
                break
            if not isinstance(chunk, (bytes, memoryview)):
                chunk = chunk.encode(self.charset)
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:

            def close():
                try:
                    self._iterator.close()
                finally:
                    self._release()

            await run_cleanup(close)
