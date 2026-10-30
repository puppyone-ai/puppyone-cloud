from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.platform.landing.registry import get_tool_spec
from src.platform.landing.service import LandingService, _openapi_to_markdown


def _service():
    return LandingService(*(SimpleNamespace() for _ in range(5)))


@pytest.mark.asyncio
async def test_tsv_with_comma_heavy_cells_preserves_the_advertised_columns():
    markdown = await _service()._parse(
        get_tool_spec("excel"), src_key="landing/id/source/input.tsv",
        content='name\tnotes\nalpha\t"one,two,three,four"\n'.encode(), ticket_id="id",
    )
    assert "| name | notes |" in markdown
    assert "| alpha | one,two,three,four |" in markdown


@pytest.mark.asyncio
async def test_csv_bom_and_pipe_headers_do_not_corrupt_the_markdown_table():
    markdown = await _service()._parse(
        get_tool_spec("excel"), src_key="landing/id/source/input.csv",
        content='\ufeffname,group|region\nalpha,west\n'.encode(), ticket_id="id",
    )
    assert "\ufeff" not in markdown
    assert "| name | group\\|region |" in markdown


@pytest.mark.asyncio
async def test_csv_starting_with_pk_is_still_text_not_a_zip_workbook():
    markdown = await _service()._parse(
        get_tool_spec("excel"), src_key="input.csv",
        content=b"PK,amount\npublic-reference,42\n", ticket_id="id",
    )
    assert "| PK | amount |" in markdown
    assert "| public-reference | 42 |" in markdown


@pytest.mark.asyncio
@pytest.mark.parametrize("source_format", ["csv", "xlsx"])
async def test_multiline_cells_remain_one_markdown_row(source_format):
    from io import BytesIO

    from openpyxl import Workbook

    if source_format == "xlsx":
        workbook = Workbook()
        workbook.active.append(["name", "notes"])
        workbook.active.append(["alpha", "first line\r\nsecond line"])
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        content = output.getvalue()
    else:
        content = b'name,notes\nalpha,"first line\r\nsecond line"\n'
    markdown = await _service()._parse(get_tool_spec("excel"),
        src_key="input." + source_format, content=content, ticket_id="id")
    assert "| alpha | first line<br>second line |" in markdown
    assert len([line for line in markdown.splitlines() if line.startswith("|")]) == 3


@pytest.mark.parametrize("content", [b"{}", b"[]", b"openapi: [invalid", json.dumps({
    "openapi": "3.0.3", "info": {"title": "Broken", "version": "1"},
    "paths": {"/pets": {"get": "not an operation object"}},
}).encode()])
def test_malformed_openapi_is_a_clear_input_error(content):
    with pytest.raises(ValueError, match="OpenAPI"):
        _openapi_to_markdown(content)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,key,content", [
    ("markdown", "input.md", b"\xff\xfe invalid UTF-8"),
    ("markdown", "input.md", b" \n\t"),
    ("excel", "input.xlsx", b"this is not an XLSX workbook"),
    ("excel", "input.csv", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1binary xls"),
])
async def test_invalid_source_never_becomes_misleading_context(kind, key, content):
    with pytest.raises(ValueError):
        await _service()._parse(get_tool_spec(kind), src_key=key, content=content, ticket_id="id")


def test_excel_registry_matches_the_website_supported_formats():
    accepted = get_tool_spec("excel").accept
    assert {".xlsx", ".csv", ".tsv"} <= set(accepted)
    assert ".xls" not in accepted


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status", [(ValueError("private source text"), 400), (RuntimeError("storage failure"), 502)])
async def test_preview_distinguishes_invalid_input_from_infrastructure_failure(monkeypatch, error, status):
    from io import BytesIO

    from fastapi import HTTPException, UploadFile
    from starlette.requests import Request

    from src.config import settings
    from src.platform.landing.router import _preview_hits, landing_preview

    monkeypatch.setattr(settings, "LANDING_INGEST_SECRET", "")
    monkeypatch.setattr(settings, "TURNSTILE_SECRET", "")
    _preview_hits.clear()

    async def preview(**_kwargs):
        raise error

    request = Request({"type": "http", "headers": [], "client": ("unit-test", 1234)})
    with pytest.raises(HTTPException) as caught:
        await landing_preview(request=request, tool_kind="openapi",
            file=UploadFile(file=BytesIO(b"{}"), filename="invalid.json"),
            turnstile_token=None, x_landing_secret=None, service=SimpleNamespace(preview=preview))
    assert caught.value.status_code == status
    assert "private source text" not in str(caught.value.detail)
