"""Shared indexed grep execution for scoped filesystem surfaces."""

from __future__ import annotations

import re
from typing import Any

from src.version_engine.write_engine.path_utils import normalize_path


class IndexedGrepError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def clean_relative_path(path: str | None) -> str:
    raw = (path or "").strip()
    if raw in ("", "/", "."):
        return ""
    try:
        return normalize_path(raw)
    except ValueError as exc:
        raise IndexedGrepError(str(exc), status_code=400) from exc


def join_scope(scope_path: str, relative_path: str) -> str:
    scope = (scope_path or "").strip("/")
    rel = (relative_path or "").strip("/")
    if not scope:
        return rel
    if not rel:
        return scope
    return f"{scope}/{rel}"


def relative_to_scope(full_path: str, scope_path: str) -> str:
    clean = (full_path or "").strip("/")
    scope = (scope_path or "").strip("/")
    if not scope:
        return clean
    if clean == scope:
        return ""
    prefix = f"{scope}/"
    if clean.startswith(prefix):
        return clean[len(prefix):]
    return clean


def matches_exclude(relative_path: str, excludes: list[Any]) -> bool:
    rel = relative_path.strip("/")
    if not rel:
        return False
    segments = rel.split("/")
    for item in excludes:
        pattern = str(item).strip("/")
        if not pattern:
            continue
        if "/" in pattern:
            if rel == pattern or rel.startswith(f"{pattern}/"):
                return True
        elif pattern in segments:
            return True
    return False


def run_indexed_grep_payload(
    *,
    project_id: str,
    scope_path: str,
    excludes: list[Any],
    ops: Any,
    pattern: str,
    path: str = "",
    regex: bool = False,
    ignore_case: bool = False,
    word_match: bool = False,
    invert_match: bool = False,
    only_matching: bool = False,
    before_context: int = 0,
    after_context: int = 0,
    limit: int = 1000,
    per_file_limit: int = 0,
    candidate_limit: int = 2000,
    pattern_max_chars: int = 2048,
    max_limit: int = 20000,
) -> dict[str, Any]:
    from src.version_engine.infrastructure.supabase.text_index_repository import (
        TextIndexRepository,
        cut_chunk_to_hits,
    )

    rel_path = clean_relative_path(path)
    if matches_exclude(rel_path, excludes):
        raise IndexedGrepError(
            f"Path is excluded from this scope: {rel_path}",
            status_code=403,
        )

    if len(pattern) > pattern_max_chars:
        raise IndexedGrepError(
            f"grep pattern exceeds {pattern_max_chars} characters",
            status_code=400,
        )

    py_pattern = pattern if regex else re.escape(pattern)
    if word_match:
        py_pattern = rf"\b(?:{py_pattern})\b"
    py_flags = re.IGNORECASE if ignore_case else 0
    try:
        matcher = re.compile(py_pattern, py_flags)
    except re.error as exc:
        raise IndexedGrepError(f"Invalid regex: {exc}", status_code=400) from exc

    combined_scope = join_scope(scope_path, rel_path).strip("/")
    safe_candidate_limit = max(1, min(int(candidate_limit), 5000))
    safe_limit = max(1, min(int(limit), max_limit))
    safe_before_context = min(max(0, int(before_context)), 100)
    safe_after_context = min(max(0, int(after_context)), 100)

    repo = TextIndexRepository()
    candidates = repo.query_indexed_grep(
        project_id=project_id,
        scope_path=combined_scope,
        pattern=pattern,
        regex=regex,
        ignore_case=ignore_case,
        candidate_limit=safe_candidate_limit,
    )

    head_commit_id = ops.get_head_commit_id(project_id) or ""
    freshness = repo.get_freshness(
        project_id=project_id,
        scope_path=combined_scope,
        head_commit_id=head_commit_id,
        rows_estimate=len(candidates),
    )

    safe_per_file_limit = max(0, int(per_file_limit))
    hits: list[dict[str, Any]] = []
    per_file_seen: dict[str, int] = {}
    truncated = False
    for cand in candidates:
        if len(hits) >= safe_limit:
            truncated = True
            break
        file_path = cand.file_path
        if matches_exclude(relative_to_scope(file_path, scope_path), excludes):
            continue
        remaining = None
        if safe_per_file_limit:
            already = per_file_seen.get(file_path, 0)
            if already >= safe_per_file_limit:
                continue
            remaining = safe_per_file_limit - already
        chunk_hits = cut_chunk_to_hits(
            chunk_text=cand.chunk_text,
            line_start=cand.line_start,
            matcher=matcher,
            invert=invert_match,
            only_matching=only_matching,
            before_context=safe_before_context,
            after_context=safe_after_context,
            per_file_remaining=remaining,
        )
        if not chunk_hits:
            continue
        for ch in chunk_hits:
            if len(hits) >= safe_limit:
                truncated = True
                break
            hits.append({
                "path": file_path,
                "line": ch["line"],
                "col": ch["col"],
                "match": ch["match"],
                "context_before": ch["context_before"],
                "context_after": ch["context_after"],
                "content_hash": cand.content_hash,
            })
            per_file_seen[file_path] = per_file_seen.get(file_path, 0) + 1

    return {
        "scope": combined_scope,
        "pattern": pattern,
        "regex": regex,
        "ignore_case": ignore_case,
        "word_match": word_match,
        "invert_match": invert_match,
        "only_matching": only_matching,
        "limit": safe_limit,
        "per_file_limit": safe_per_file_limit,
        "candidate_limit": candidate_limit,
        "candidates_examined": len(candidates),
        "hits": hits,
        "truncated": truncated,
        "index_status": freshness.status,
        "index_freshness": {
            "indexed_commit_id": freshness.indexed_commit_id,
            "head_commit_id": freshness.head_commit_id,
            "commits_behind": freshness.commits_behind,
        },
        "head_commit_id": head_commit_id,
    }
