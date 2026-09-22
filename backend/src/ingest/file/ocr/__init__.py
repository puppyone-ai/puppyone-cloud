"""
OCR Provider Module

Pluggable OCR service abstraction layer.
Supports multiple OCR providers: MineRU, Reducto, DeepSeek, etc.
"""

from src.ingest.file.ocr.base import (
    OCRExternalJob,
    OCRExternalJobCompletion,
    OCRProvider,
    OCRProviderCleanupResult,
    OCRProviderCleanupState,
    ParsedDocument,
    parse_document_with_external_lifecycle,
)
from src.ingest.file.ocr.external_cleanup import (
    ExternalIngestCleanup,
    ExternalIngestCleanupResult,
    ExternalIngestCleanupSnapshot,
)
from src.ingest.file.ocr.factory import OCRProviderFactory, get_ocr_provider
from src.ingest.file.ocr.lifecycle import run_ocr_lifecycle_under_project_lease

__all__ = [
    "ExternalIngestCleanup",
    "ExternalIngestCleanupResult",
    "ExternalIngestCleanupSnapshot",
    "OCRExternalJob",
    "OCRExternalJobCompletion",
    "OCRProvider",
    "OCRProviderCleanupResult",
    "OCRProviderCleanupState",
    "OCRProviderFactory",
    "ParsedDocument",
    "get_ocr_provider",
    "parse_document_with_external_lifecycle",
    "run_ocr_lifecycle_under_project_lease",
]
