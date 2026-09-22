"""Tool registry — the plug-and-play core of the SEO-tools wrapper.

Adding a new "X → MCP" tool is one entry here (+ frontend config + i18n copy);
no new endpoint or orchestration code. Each spec tells the generic
preview/claim flow how to parse the input and how to shape the resulting repo.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSpec:
    kind: str
    # Accepted content-types / extensions (advisory; the website enforces too).
    accept: tuple[str, ...]
    # How preview turns bytes into repo content:
    #   "ocr_parse"   — OCR the document into markdown (PDF, images, docx...)
    #   "passthrough" — already text; store as-is (markdown, txt, ...)
    #   (future) "openapi", "url" — handled by their own branches in service._parse
    parser: str
    # Leaf scope/folder the content lands in. Kept a single leaf scope so writes
    # always target the narrowest scope (avoids sub-scope graft shadowing).
    scope_path: str = "doc"
    # MCP exposure default for anonymous-origin artifacts.
    readonly: bool = True
    # Extension of the produced repo file.
    output_ext: str = ".md"
    # Appended to the derived project name, e.g. " (PDF → MCP)".
    name_suffix: str = ""


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "pdf": ToolSpec(
        kind="pdf",
        accept=("application/pdf", ".pdf"),
        # Text-layer extraction via PyMuPDF (no API key); OCR fallback only for
        # scanned/image-only PDFs when an OCR provider is configured.
        parser="pdf",
        scope_path="doc",
        readonly=True,
        output_ext=".md",
        name_suffix=" (PDF → MCP)",
    ),
    # Text-native kind: lets the full preview→claim flow be exercised without
    # depending on OCR being enabled. Also the template for excel/openapi/etc.
    "markdown": ToolSpec(
        kind="markdown",
        accept=("text/markdown", "text/plain", ".md", ".markdown", ".txt"),
        parser="passthrough",
        scope_path="doc",
        readonly=True,
        output_ext=".md",
        name_suffix=" (Markdown → MCP)",
    ),
    "openapi": ToolSpec(
        kind="openapi",
        accept=("application/json", "application/yaml", "text/yaml", ".json", ".yaml", ".yml"),
        parser="openapi",
        scope_path="api",
        readonly=True,
        output_ext=".md",
        name_suffix=" (OpenAPI → MCP)",
    ),
    "excel": ToolSpec(
        kind="excel",
        accept=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            ".xlsx",
            ".xls",
        ),
        parser="excel",
        scope_path="data",
        readonly=True,
        output_ext=".md",
        name_suffix=" (Excel → MCP)",
    ),
}


def get_tool_spec(kind: str) -> ToolSpec:
    spec = TOOL_REGISTRY.get((kind or "").strip().lower())
    if spec is None:
        raise KeyError(
            f"Unknown tool_kind '{kind}'. Known: {sorted(TOOL_REGISTRY)}"
        )
    return spec
