"""Project-leased orchestration for durable OCR provider jobs."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from src.infra.execution.runner import await_owned
from src.infra.file_processing.ocr.base import (
    ExternalJobCreatedHook,
    ExternalJobTerminalHook,
    OCRProvider,
    ParsedDocument,
    parse_document_with_external_lifecycle,
)


class ProjectWriteLeaseFactory(Protocol):
    """Structural subset of the Project write-lease constructor."""

    def __call__(
        self,
        project_id: str,
        operation: str,
        **kwargs: Any,
    ) -> AbstractAsyncContextManager[Any]: ...


async def run_ocr_lifecycle_under_project_lease(
    *,
    lease_factory: ProjectWriteLeaseFactory,
    project_id: str,
    provider: OCRProvider,
    file_url: str,
    data_id: str | None = None,
    on_created: ExternalJobCreatedHook | None = None,
    on_terminal: ExternalJobTerminalHook | None = None,
    operation: str = "etl.ocr_external",
    lease_kwargs: dict[str, Any] | None = None,
) -> ParsedDocument:
    """Create, persist, wait, and materialize OCR under one Project lease.

    ``ProjectWriteLease`` can be passed directly as ``lease_factory``.  Its
    normal active-lease reuse semantics make this helper safe both at a worker
    entry point and inside an already leased job.
    """

    async with lease_factory(project_id, operation, **(lease_kwargs or {})):
        lifecycle = asyncio.create_task(
            parse_document_with_external_lifecycle(
                provider,
                file_url=file_url,
                data_id=data_id,
                on_created=on_created,
                on_terminal=on_terminal,
            )
        )
        return await await_owned(lifecycle)
