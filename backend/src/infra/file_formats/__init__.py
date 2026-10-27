"""File format registry — backend single source of truth.

Mirrors `frontend/lib/fileFormats/registry.ts`. Adding a format =
add an entry to `FILE_FORMATS` in `registry.py` and the matching
entry in the frontend file. The two files are intentionally
plain-data so they're trivial to keep in sync.

Public API:
- ``FILE_FORMATS``: full list of registered formats.
- ``detect_mime(name)``: filename → MIME string.
- ``detect_node_type(name)``: filename → tree node type
  (``"folder" | "json" | "markdown" | "file"``).
- ``detect_ingest_type(name)``: filename → ingest pipeline bucket
  (``IngestType``).
"""

from .registry import (
    FILE_FORMATS,
    FormatSpec,
    detect_ingest_type,
    detect_mime,
    detect_node_type,
    is_text_format,
)

__all__ = [
    "FILE_FORMATS",
    "FormatSpec",
    "detect_ingest_type",
    "detect_mime",
    "detect_node_type",
    "is_text_format",
]
