"""CLI AP-FS search: existing protocol behavior over admitted repository operations."""

from __future__ import annotations

import fnmatch
import re

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel as _BaseModel

from src.common_schemas import ApiResponse
from src.file_operations.operation_adapter import ProductOperationAdapter
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter
from src.platform.access.filesystem.errors import ScopedFsError
from src.platform.access.filesystem.indexed_grep import IndexedGrepError, run_indexed_grep_payload
from src.platform.access.filesystem.service import ScopedFsService

from .context import (
    _assert_not_excluded,
    _basename,
    _clean_relative,
    _filter_entries,
    _is_hidden_path,
    _join_scope,
    _matches_exclude,
    _ops_list_tree,
    _ops_read_file,
    _ops_stat,
    _query_bool,
    _query_int,
    _query_limited_int,
    _relative_to_scope,
    _resolve_auth,
    _scope_payload,
    _scoped_fs_context,
    _scoped_fs_error,
)


router = APIRouter()


_GREP_DEFAULT_LIMIT = 1000


_GREP_MAX_LIMIT = 20000


_GREP_DEFAULT_FILE_LIMIT = 5000


_GREP_MAX_FILE_LIMIT = 50000


_GREP_DEFAULT_BYTE_LIMIT = 16 * 1024 * 1024


_GREP_MAX_BYTE_LIMIT = 256 * 1024 * 1024


_GREP_PATTERN_MAX_CHARS = 2048


_BINARY_SAMPLE_BYTES = 4096


_TEXT_MIME_EXACT = frozenset(
    {
        "application/dart",
        "application/graphql",
        "application/javascript",
        "application/json",
        "application/sql",
        "application/toml",
        "application/typescript",
        "application/vnd.coffeescript",
        "application/x-bat",
        "application/x-csh",
        "application/x-ipynb+json",
        "application/x-ndjson",
        "application/x-php",
        "application/x-powershell",
        "application/x-sh",
        "application/x-subrip",
        "application/x-tcl",
        "application/x-tex",
        "application/xml",
        "application/yaml",
        "image/svg+xml",
    }
)


_TEXT_BASENAMES = frozenset(
    {
        ".babelrc",
        ".dockerignore",
        ".env",
        ".eslintrc",
        ".gitattributes",
        ".gitignore",
        ".npmrc",
        ".nvmrc",
        ".prettierrc",
        ".python-version",
        ".tool-versions",
        "Dockerfile",
        "Makefile",
        "README",
    }
)


_TEXT_BASENAMES_LOWER = frozenset(name.lower() for name in _TEXT_BASENAMES)


def _looks_text_entry(entry) -> bool:
    if getattr(entry, "type", "") in {"json", "markdown"}:
        return True
    mime = (getattr(entry, "mime_type", "") or "").lower()
    if mime.startswith("text/"):
        return True
    if mime in _TEXT_MIME_EXACT:
        return True
    base = _basename(getattr(entry, "path", "") or getattr(entry, "name", ""))
    return base.lower() in _TEXT_BASENAMES_LOWER


def _looks_binary(content: bytes) -> bool:
    sample = content[:_BINARY_SAMPLE_BYTES]
    return b"\x00" in sample


def _decode_grep_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")


def _split_grep_globs(value: str | None) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    return [item.strip() for item in str(value).splitlines() if item.strip()]


def _matches_grep_glob(path: str, pattern: str) -> bool:
    clean = path.strip("/")
    base = _basename(clean)
    return fnmatch.fnmatchcase(clean, pattern) or fnmatch.fnmatchcase(base, pattern)


def _matches_any_grep_glob(path: str, patterns: list[str]) -> bool:
    return any(_matches_grep_glob(path, pattern) for pattern in patterns)


def _matches_exclude_dir_glob(path: str, patterns: list[str]) -> bool:
    parts = [part for part in path.strip("/").split("/")[:-1] if part]
    return any(fnmatch.fnmatchcase(part, pattern) for part in parts for pattern in patterns)


def _grep_file_depth(path: str, root_path: str = "") -> int:
    clean = path.strip("/")
    root = root_path.strip("/")
    if root and clean == root:
        return 0
    rel = clean[len(root) + 1 :] if root and clean.startswith(f"{root}/") else clean
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    if not parent:
        return 0
    return len([part for part in parent.split("/") if part])


def _grep_matcher(pattern: str, *, regex: bool, ignore_case: bool):
    if len(pattern) > _GREP_PATTERN_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"grep pattern exceeds {_GREP_PATTERN_MAX_CHARS} characters",
        )

    if regex:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc

        def _match(line: str) -> list[tuple[int, int]]:
            spans: list[tuple[int, int]] = []
            for match in compiled.finditer(line):
                start, end = match.start(), match.end()
                if start == end:
                    continue
                spans.append((start, end))
            return spans

        return _match

    needle = pattern.casefold() if ignore_case else pattern

    def _fixed_match(line: str) -> list[tuple[int, int]]:
        if needle == "":
            return [(0, 0)]
        haystack = line.casefold() if ignore_case else line
        spans: list[tuple[int, int]] = []
        start_at = 0
        while True:
            index = haystack.find(needle, start_at)
            if index < 0:
                break
            spans.append((index, index + len(pattern)))
            start_at = index + max(len(needle), 1)
        return spans

    return _fixed_match


@router.get("/grep", response_model=ApiResponse)
async def grep(
    pattern: str = Query(..., description="Fixed string or regex pattern to match"),
    path: str = Query("", description="File or directory path relative to the access point scope"),
    ref: str = Query(
        "",
        description="Reserved; leave empty to read the native repository HEAD.",
    ),
    regex: bool = Query(False, description="Treat pattern as a regular expression"),
    ignore_case: bool = Query(False, description="Case-insensitive matching"),
    invert_match: bool = Query(False, description="Select non-matching lines"),
    only_matching: bool = Query(False, description="Return only the matching text"),
    include_hidden: bool = Query(False, description="Include entries whose names begin with '.'"),
    include: str = Query("", description="Newline-separated file glob patterns to include"),
    exclude: str = Query("", description="Newline-separated file glob patterns to exclude"),
    exclude_dir: str = Query(
        "", description="Newline-separated directory glob patterns to exclude"
    ),
    max_depth: int = Query(
        -1, description="Maximum recursion depth for directories, -1 = unlimited"
    ),
    max_count: int = Query(
        0, description="Maximum matching lines returned per file, 0 = unlimited"
    ),
    require_file_list: bool = Query(
        False, description="Require every candidate file in files[], including zero-match files"
    ),
    before_context: int = Query(0, description="Context lines before each match"),
    after_context: int = Query(0, description="Context lines after each match"),
    include_offsets: bool = Query(False, description="Include byte offsets in match metadata"),
    limit: int = Query(_GREP_DEFAULT_LIMIT, description="Maximum matching lines returned"),
    max_files: int = Query(_GREP_DEFAULT_FILE_LIMIT, description="Maximum file candidates scanned"),
    max_bytes: int = Query(
        _GREP_DEFAULT_BYTE_LIMIT, description="Maximum decoded text bytes scanned"
    ),
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    project_id, _auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="grep",
    )
    ops = ops.for_grant(_auth["_runtime_grant"])
    rel_path = _clean_relative(path)
    _assert_not_excluded(rel_path, scope)
    max_depth = _query_int(max_depth, -1)
    include_hidden = _query_bool(include_hidden)
    invert_match = _query_bool(invert_match)
    only_matching = _query_bool(only_matching)
    include_offsets = _query_bool(include_offsets)
    require_file_list = _query_bool(require_file_list)
    per_file_limit = max(0, _query_int(max_count, 0))
    before_context = min(max(0, _query_int(before_context, 0)), 100)
    after_context = min(max(0, _query_int(after_context, 0)), 100)
    include_patterns = _split_grep_globs(include)
    exclude_patterns = _split_grep_globs(exclude)
    exclude_dir_patterns = _split_grep_globs(exclude_dir)
    safe_limit = _query_limited_int(limit, _GREP_DEFAULT_LIMIT, _GREP_MAX_LIMIT)
    safe_file_limit = _query_limited_int(max_files, _GREP_DEFAULT_FILE_LIMIT, _GREP_MAX_FILE_LIMIT)
    safe_byte_limit = _query_limited_int(max_bytes, _GREP_DEFAULT_BYTE_LIMIT, _GREP_MAX_BYTE_LIMIT)
    match_line = _grep_matcher(pattern, regex=regex, ignore_case=ignore_case)

    if isinstance(ref, str) and ref:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "snapshot_ref_retired",
                "message": "This filesystem endpoint reads native repository HEAD.",
            },
        )

    full_path = _join_scope(scope["path"], rel_path)
    target = _ops_stat(ops, project_id, scope, rel_path)
    if target is None and rel_path:
        raise HTTPException(status_code=404, detail=f"Path not found: {rel_path}")
    target_type = target.type if target and target.type != "folder" else "folder"

    if not require_file_list:
        indexed_result = _try_indexed_grep_legacy_response(
            project_id=project_id,
            scope=scope,
            ops=ops,
            body=_GrepIndexedRequest(
                pattern=pattern,
                path=rel_path,
                regex=regex,
                ignore_case=ignore_case,
                invert_match=invert_match,
                only_matching=only_matching,
                before_context=before_context,
                after_context=after_context,
                limit=safe_limit,
                per_file_limit=per_file_limit,
                candidate_limit=safe_file_limit,
            ),
            rel_path=rel_path,
            version_path=full_path,
            target_type=target_type,
            include_hidden=include_hidden,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            exclude_dir_patterns=exclude_dir_patterns,
            max_depth=max_depth,
            include_offsets=include_offsets,
            safe_file_limit=safe_file_limit,
            safe_byte_limit=safe_byte_limit,
        )
        if indexed_result is not None:
            return ApiResponse.success(data=indexed_result)

    truncated = False
    truncation_reason = ""

    def mark_truncated(reason: str) -> None:
        nonlocal truncated, truncation_reason
        truncated = True
        if not truncation_reason:
            truncation_reason = reason

    if target and target.type != "folder":
        candidates = [target]
    else:
        tree_entries = _filter_entries(
            _ops_list_tree(
                ops,
                project_id,
                scope,
                rel_path,
                max_depth=max_depth,
                include_size=False,
                max_entries=safe_file_limit + 1,
            ),
            scope,
            include_hidden=include_hidden,
        )
        if len(tree_entries) > safe_file_limit:
            mark_truncated("file_limit_exceeded")
            tree_entries = tree_entries[:safe_file_limit]
        candidates = [entry for entry in tree_entries if entry.type != "folder"]

    matches: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    content_cache: dict[str, bytes] = {}
    scanned_files = 0
    scanned_bytes = 0
    skipped = {
        "non_text": 0,
        "binary": 0,
        "too_large": 0,
        "read_errors": 0,
    }

    for entry in candidates:
        rel_entry_path = _relative_to_scope(entry.path, scope["path"])
        if _matches_exclude(rel_entry_path, scope.get("exclude") or []):
            continue
        if include_patterns and not _matches_any_grep_glob(rel_entry_path, include_patterns):
            continue
        if exclude_patterns and _matches_any_grep_glob(rel_entry_path, exclude_patterns):
            continue
        if exclude_dir_patterns and _matches_exclude_dir_glob(rel_entry_path, exclude_dir_patterns):
            continue
        if not _looks_text_entry(entry):
            skipped["non_text"] += 1
            continue

        content_hash = getattr(entry, "content_hash", None) or ""
        try:
            if content_hash and content_hash in content_cache:
                content = content_cache[content_hash]
            else:
                content = _ops_read_file(ops, project_id, scope, rel_entry_path)
                if content_hash:
                    content_cache[content_hash] = content
        except FileNotFoundError:
            skipped["read_errors"] += 1
            mark_truncated("read_error")
            continue
        except Exception:
            skipped["read_errors"] += 1
            mark_truncated("read_error")
            continue

        if _looks_binary(content):
            skipped["binary"] += 1
            continue
        if scanned_bytes + len(content) > safe_byte_limit:
            skipped["too_large"] += 1
            mark_truncated("byte_limit_exceeded")
            break

        scanned_files += 1
        scanned_bytes += len(content)
        text = _decode_grep_text(content)
        need_line_offsets = include_offsets or before_context > 0 or after_context > 0
        if need_line_offsets:
            raw_lines = text.splitlines(keepends=True)
            line_items: list[tuple[str, int | None]] = []
            byte_cursor = 0
            for raw_line in raw_lines:
                clean_line = raw_line.rstrip("\r\n")
                line_items.append((clean_line, byte_cursor))
                byte_cursor += len(raw_line.encode("utf-8"))
            if text and not raw_lines:
                line_items.append((text, 0))
        else:
            line_items = [(line, None) for line in text.splitlines()]

        file_match_count = 0
        for line_number, (line_text, line_byte_offset) in enumerate(line_items, start=1):
            spans = match_line(line_text)
            matched = bool(spans)
            if invert_match:
                matched = not matched
            if not matched:
                continue
            if only_matching and not invert_match and spans:
                output_spans = spans
            else:
                first_span = spans[0] if spans else (None, None)
                output_spans = [first_span]

            for match_start, match_end in output_spans:
                match_text = (
                    line_text[match_start:match_end]
                    if isinstance(match_start, int) and isinstance(match_end, int)
                    else ""
                )
                match_byte_offset = None
                if isinstance(line_byte_offset, int):
                    match_byte_offset = (
                        line_byte_offset + len(line_text[:match_start].encode("utf-8"))
                        if isinstance(match_start, int)
                        else line_byte_offset
                    )
                before_lines = []
                if before_context:
                    start_index = max(0, line_number - 1 - before_context)
                    for ctx_index in range(start_index, line_number - 1):
                        before_lines.append(
                            {
                                "line_number": ctx_index + 1,
                                "line_text": line_items[ctx_index][0],
                                "byte_offset": line_items[ctx_index][1],
                            }
                        )
                after_lines = []
                if after_context:
                    end_index = min(len(line_items), line_number + after_context)
                    for ctx_index in range(line_number, end_index):
                        after_lines.append(
                            {
                                "line_number": ctx_index + 1,
                                "line_text": line_items[ctx_index][0],
                                "byte_offset": line_items[ctx_index][1],
                            }
                        )
                matches.append(
                    {
                        "path": rel_entry_path,
                        "version_path": _join_scope(scope["path"], rel_entry_path),
                        "line_number": line_number,
                        "line_text": line_text,
                        "match_start": match_start,
                        "match_end": match_end,
                        "match_text": match_text,
                        "byte_offset": line_byte_offset,
                        "match_byte_offset": match_byte_offset,
                        "before_context": before_lines,
                        "after_context": after_lines,
                        "content_hash": content_hash or None,
                    }
                )
                file_match_count += 1
                if len(matches) >= safe_limit:
                    mark_truncated("result_limit_exceeded")
                    break
                if per_file_limit and file_match_count >= per_file_limit:
                    break
            if truncated and truncation_reason == "result_limit_exceeded":
                break
            if per_file_limit and file_match_count >= per_file_limit:
                break
        files.append(
            {
                "path": rel_entry_path,
                "version_path": _join_scope(scope["path"], rel_entry_path),
                "match_count": file_match_count,
                "content_hash": content_hash or None,
            }
        )
        if truncated and truncation_reason == "result_limit_exceeded":
            break

    scope_head_commit_id = ops.get_scope_head_commit_id(project_id, scope["path"])
    matched_files = len([item for item in files if item.get("match_count", 0) > 0])
    return ApiResponse.success(
        data={
            "pattern": pattern,
            "path": rel_path,
            "version_path": full_path,
            "scope": _scope_payload(scope),
            "target_type": target_type,
            "regex": regex,
            "ignore_case": ignore_case,
            "invert_match": invert_match,
            "only_matching": only_matching,
            "include_offsets": include_offsets,
            "require_file_list": require_file_list,
            "include": include_patterns,
            "exclude": exclude_patterns,
            "exclude_dir": exclude_dir_patterns,
            "limit": safe_limit,
            "max_count": per_file_limit,
            "before_context": before_context,
            "after_context": after_context,
            "max_files": safe_file_limit,
            "max_bytes": safe_byte_limit,
            "returned_count": len(matches),
            "matched_files": matched_files,
            "candidate_files": len(candidates),
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
            "skipped": skipped,
            "complete": not truncated,
            "truncated": truncated,
            "truncation_reason": truncation_reason,
            "files": files,
            "matches": matches,
            "head_commit_id": scope_head_commit_id,
            "scope_head_commit_id": scope_head_commit_id,
        }
    )


class _GrepIndexedRequest(_BaseModel):
    """Body for ``POST /ap-fs/grep-indexed``.

    Flags mirror a useful subset of the legacy ``GET /grep``
    parameters; we drop the file-walk flags (``include`` /
    ``exclude_dir`` / ``max_depth`` / ``max_files`` / ``max_bytes``)
    because the index is the scan boundary now — anything in scope
    that the indexer hasn't stored is by definition not findable
    via this path and the caller falls back to the legacy endpoint.
    """

    pattern: str
    path: str = ""
    regex: bool = False
    ignore_case: bool = False
    word_match: bool = False
    invert_match: bool = False
    only_matching: bool = False
    before_context: int = 0
    after_context: int = 0
    limit: int = _GREP_DEFAULT_LIMIT
    per_file_limit: int = 0
    candidate_limit: int = 2000


def _run_grep_indexed_payload(
    *,
    project_id: str,
    scope: dict,
    ops: ProductOperationAdapter,
    body: _GrepIndexedRequest,
) -> dict[str, Any]:
    try:
        return run_indexed_grep_payload(
            project_id=project_id,
            scope_path=scope["path"],
            excludes=scope.get("exclude") or [],
            ops=ops,
            pattern=body.pattern,
            path=body.path,
            regex=body.regex,
            ignore_case=body.ignore_case,
            word_match=body.word_match,
            invert_match=body.invert_match,
            only_matching=body.only_matching,
            before_context=body.before_context,
            after_context=body.after_context,
            limit=body.limit,
            per_file_limit=body.per_file_limit,
            candidate_limit=body.candidate_limit,
            pattern_max_chars=_GREP_PATTERN_MAX_CHARS,
            max_limit=_GREP_MAX_LIMIT,
        )
    except IndexedGrepError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _try_indexed_grep_legacy_response(
    *,
    project_id: str,
    scope: dict,
    ops: ProductOperationAdapter,
    body: _GrepIndexedRequest,
    rel_path: str,
    version_path: str,
    target_type: str,
    include_hidden: bool,
    include_patterns: list[str],
    exclude_patterns: list[str],
    exclude_dir_patterns: list[str],
    max_depth: int,
    include_offsets: bool,
    safe_file_limit: int,
    safe_byte_limit: int,
) -> dict[str, Any] | None:
    try:
        envelope = _run_grep_indexed_payload(
            project_id=project_id,
            scope=scope,
            ops=ops,
            body=body,
        )
    except HTTPException:
        raise
    except Exception:
        return None

    if envelope.get("index_status") != "indexed":
        return None

    matches: list[dict[str, Any]] = []
    files_by_path: dict[str, dict[str, Any]] = {}
    skipped_by_filter = 0
    for hit in envelope.get("hits") or []:
        hit_path = str(hit.get("path") or "")
        hit_rel_path = _relative_to_scope(hit_path, scope["path"])
        if not include_hidden and _is_hidden_path(hit_rel_path):
            skipped_by_filter += 1
            continue
        if max_depth >= 0 and _grep_file_depth(hit_rel_path, rel_path) > max_depth:
            skipped_by_filter += 1
            continue
        if include_patterns and not _matches_any_grep_glob(hit_rel_path, include_patterns):
            skipped_by_filter += 1
            continue
        if exclude_patterns and _matches_any_grep_glob(hit_rel_path, exclude_patterns):
            skipped_by_filter += 1
            continue
        if exclude_dir_patterns and _matches_exclude_dir_glob(hit_rel_path, exclude_dir_patterns):
            skipped_by_filter += 1
            continue
        content_hash = hit.get("content_hash") or None
        line_text = str(hit.get("match") or "")
        col = max(0, int(hit.get("col") or 1) - 1)
        match_payload: dict[str, Any] = {
            "path": hit_rel_path,
            "version_path": _join_scope(scope["path"], hit_rel_path),
            "line_number": hit.get("line") or 0,
            "line_text": line_text,
            "match_start": col,
            "match_end": col + len(line_text),
            "match_text": line_text,
            "byte_offset": None,
            "match_byte_offset": col if include_offsets else None,
            "before_context": [{"line_text": text} for text in (hit.get("context_before") or [])],
            "after_context": [{"line_text": text} for text in (hit.get("context_after") or [])],
            "content_hash": content_hash,
        }
        matches.append(match_payload)
        file_payload = files_by_path.setdefault(
            hit_rel_path,
            {
                "path": hit_rel_path,
                "version_path": _join_scope(scope["path"], hit_rel_path),
                "match_count": 0,
                "content_hash": content_hash,
            },
        )
        file_payload["match_count"] += 1

    scope_head_commit_id = ops.get_scope_head_commit_id(project_id, scope["path"])
    truncated = bool(envelope.get("truncated"))
    truncation_reason = "indexed_truncated" if truncated else ""
    skipped = {
        "non_text": 0,
        "binary": 0,
        "too_large": 0,
        "read_errors": 0,
    }
    if skipped_by_filter:
        skipped["filtered"] = skipped_by_filter

    return {
        "pattern": body.pattern,
        "path": rel_path,
        "version_path": version_path,
        "scope": _scope_payload(scope),
        "target_type": target_type,
        "regex": body.regex,
        "ignore_case": body.ignore_case,
        "invert_match": body.invert_match,
        "only_matching": body.only_matching,
        "include_offsets": include_offsets,
        "include": include_patterns,
        "exclude": exclude_patterns,
        "exclude_dir": exclude_dir_patterns,
        "limit": envelope.get("limit") or body.limit,
        "max_count": envelope.get("per_file_limit") or body.per_file_limit,
        "before_context": body.before_context,
        "after_context": body.after_context,
        "max_files": safe_file_limit,
        "max_bytes": safe_byte_limit,
        "returned_count": len(matches),
        "matched_files": len(files_by_path),
        "candidate_files": envelope.get("candidates_examined") or 0,
        "scanned_files": 0,
        "scanned_bytes": 0,
        "skipped": skipped,
        "complete": not truncated,
        "truncated": truncated,
        "truncation_reason": truncation_reason,
        "files": list(files_by_path.values()),
        "matches": matches,
        "head_commit_id": scope_head_commit_id,
        "scope_head_commit_id": scope_head_commit_id,
        "index_status": envelope.get("index_status"),
        "index_freshness": envelope.get("index_freshness"),
        "search_backend": "indexed",
    }


@router.post("/grep-indexed", response_model=ApiResponse)
async def grep_indexed(
    body: _GrepIndexedRequest,
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    """Indexed grep against the server's text index.

    Returns hits keyed by ``content_hash`` for diagnostics and raw
    indexed consumers. The response also carries ``index_status`` so
    callers can tell whether the index is authoritative:

      - ``indexed`` — index is at HEAD; results are authoritative.
      - ``stale``   — index is behind HEAD.
      - ``missing`` — no rows for this scope.
    """
    project_id, _auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="grep",
    )
    ops = ops.for_grant(_auth["_runtime_grant"])
    return ApiResponse.success(
        data=_run_grep_indexed_payload(
            project_id=project_id,
            scope=scope,
            ops=ops,
            body=body,
        )
    )


@router.get("/find", response_model=ApiResponse)
async def find_index(
    name: str = Query("", description="fnmatch-style glob over the basename"),
    iname: str = Query("", description="case-insensitive fnmatch-style glob over the basename"),
    path: str = Query("", description="Subpath under the scope to narrow the search"),
    path_glob: str = Query("", description="fnmatch-style glob over the scope-relative path"),
    mime: str = Query("", description="Optional mime-type prefix filter (e.g. 'text/')"),
    type_: str = Query(
        "any",
        alias="type",
        description="'file', 'folder', or 'any'",
    ),
    conditions: str = Query("", description="JSON array of find predicates from CLI/MCP surfaces"),
    mindepth: int = Query(0, description="Minimum result depth relative to the search root"),
    max_depth: int = Query(
        -1, description="Maximum result depth relative to the search root, -1 = unlimited"
    ),
    include_hidden: bool = Query(True, description="Include entries whose names begin with '.'"),
    limit: int = Query(1000, ge=1, le=50000),
    x_access_key: str | None = Header(None, alias="X-Access-Key"),
    x_puppyone_user: str | None = Header(None, alias="X-PuppyOne-User"),
    x_puppy_client: str | None = Header(None, alias="X-Puppy-Client"),
    ops: ProductOperationAdapter = Depends(get_product_operation_adapter),
):
    """Canonical scoped find.

    The route owns traversal, filtering, truncation, and future
    index/fallback policy. CLI and MCP clients should map their surface
    syntax into this request shape rather than filtering locally.
    """

    project_id, auth, scope = await _resolve_auth(
        x_access_key,
        x_puppyone_user,
        x_puppy_client,
        command="find",
    )
    ops = ops.for_grant(auth["_runtime_grant"])
    name_value = name if isinstance(name, str) else ""
    iname_value = iname if isinstance(iname, str) else ""
    path_glob_value = path_glob if isinstance(path_glob, str) else ""
    mime_value = mime if isinstance(mime, str) else ""
    type_value = type_ if isinstance(type_, str) else "any"
    conditions_value = conditions if isinstance(conditions, str) else ""
    if mime_value:
        # The previous route exposed a file-index-only mime filter. It is not
        # part of the canonical FS find contract; keep the error explicit so
        # callers do not assume MCP/CLI parity for it.
        raise HTTPException(
            status_code=400, detail="mime filtering is not supported by canonical find"
        )
    service = ScopedFsService(ops=ops, commands=None)  # type: ignore[arg-type]
    ctx = _scoped_fs_context(
        access_key=x_access_key,
        project_id=project_id,
        auth=auth,
        scope=scope,
    )
    try:
        result = await service.find(
            ctx,
            path=_clean_relative(path),
            name=name_value,
            iname=iname_value,
            path_glob=path_glob_value,
            type=type_value,
            conditions=conditions_value,
            mindepth=mindepth,
            max_depth=max_depth,
            limit=limit,
            include_hidden=_query_bool(include_hidden, True),
        )
    except ScopedFsError as exc:
        raise _scoped_fs_error(exc) from exc
    result["scope"] = _scope_payload(scope)
    result["head_commit_id"] = ops.get_head_commit_id(project_id)
    return ApiResponse.success(data=result)
