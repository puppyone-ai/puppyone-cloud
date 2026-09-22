"""Scoped filesystem command implementation."""

from __future__ import annotations

import fnmatch
import json
import re
from typing import Any

from src.version_engine.adapters.product.commands import VersionWriteCommandService
from src.version_engine.adapters.product.operation_adapter import ProductOperationAdapter
from src.version_engine.read.tree_reader import VersionEntry, detect_type
from src.version_engine.write_engine.errors import ConcurrentMutationError
from src.version_engine.write_engine.path_utils import normalize_path

from .context import ScopedFsContext
from .errors import ScopedFsError, ScopedFsNotFound, ScopedFsPermissionDenied
from .capabilities import SEMANTICS
from .indexed_grep import IndexedGrepError, relative_to_scope, run_indexed_grep_payload
from .registry import FS_TOOL_BY_NAME
from .policy import default_mcp_fs_allowed_tools


_DEFAULT_TREE_LIMIT = 5000
_MAX_TREE_LIMIT = 50000
_DEFAULT_GREP_LIMIT = 1000
_MAX_GREP_LIMIT = 20000
_DEFAULT_GREP_MAX_FILES = 5000
_MAX_GREP_MAX_FILES = 50000
_DEFAULT_GREP_MAX_BYTES = 16 * 1024 * 1024
_MAX_GREP_MAX_BYTES = 256 * 1024 * 1024


class ScopedFsService:
    def __init__(
        self,
        ops: ProductOperationAdapter,
        commands: VersionWriteCommandService,
    ):
        self.ops = ops
        self.commands = commands

    async def call(
        self,
        ctx: ScopedFsContext,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name not in FS_TOOL_BY_NAME:
            raise ScopedFsError("UNKNOWN_TOOL", f"Unknown filesystem tool: {name}")
        spec = FS_TOOL_BY_NAME[name]
        if spec.access in {"write", "delete"} and not ctx.writable:
            raise ScopedFsPermissionDenied(f"Tool {name} requires a writable MCP endpoint scope")
        allowed_tools = ctx.allowed_tools
        if allowed_tools is None:
            allowed_tools = default_mcp_fs_allowed_tools(writable=ctx.writable)
        if name not in allowed_tools:
            raise ScopedFsPermissionDenied(f"Tool {name} is disabled for this MCP endpoint")

        args = arguments or {}
        method_name = name[3:] if name.startswith("fs_") else name
        method = getattr(self, method_name)
        return await method(ctx, **args)

    async def semantics(self, ctx: ScopedFsContext) -> dict[str, Any]:
        return {
            "fs_semantics": SEMANTICS,
            "scope": self._scope_payload(ctx),
        }

    async def ls(
        self,
        ctx: ScopedFsContext,
        path: str = "",
        include_hidden: bool = False,
        include_size: bool = False,
    ) -> dict[str, Any]:
        rel = self._clean_path(ctx, path)
        target = self._stat(ctx, rel, include_size=include_size)
        if target is None and rel:
            raise ScopedFsNotFound(f"Path not found: {rel}")
        if target and target.type != "folder":
            entries = [target]
        else:
            entries = self._filter_entries(
                ctx,
                self.ops.list_dir_in_scope(ctx.project_id, ctx.scope_path, rel, include_size=include_size),
                include_hidden=include_hidden,
            )
        return {
            "path": rel,
            "scope": self._scope_payload(ctx),
            "target_type": target.type if target else "folder",
            "entries": [self._entry_payload(ctx, entry) for entry in entries],
            "head_commit_id": self._head(ctx),
        }

    async def tree(
        self,
        ctx: ScopedFsContext,
        path: str = "",
        max_depth: int = -1,
        limit: int = _DEFAULT_TREE_LIMIT,
        include_hidden: bool = False,
        include_size: bool = False,
        directories_only: bool = False,
    ) -> dict[str, Any]:
        rel = self._clean_path(ctx, path)
        safe_limit = self._bounded_int(limit, _DEFAULT_TREE_LIMIT, _MAX_TREE_LIMIT)
        target = self._stat(ctx, rel, include_size=include_size)
        if target is None and rel:
            raise ScopedFsNotFound(f"Path not found: {rel}")
        if target and target.type != "folder":
            raw_entries = [] if directories_only else [target]
            truncated = False
        else:
            raw_entries = self.ops.list_tree_in_scope(
                ctx.project_id,
                ctx.scope_path,
                rel,
                max_depth=max_depth,
                include_size=include_size,
                max_entries=safe_limit + 1,
            )
            raw_entries = self._filter_entries(ctx, raw_entries, include_hidden=include_hidden)
            if directories_only:
                raw_entries = [entry for entry in raw_entries if entry.type == "folder"]
            truncated = len(raw_entries) > safe_limit
            if truncated:
                raw_entries = raw_entries[:safe_limit]
        entries = [self._entry_payload(ctx, entry) for entry in raw_entries]
        return {
            "path": rel,
            "scope": self._scope_payload(ctx),
            "target_type": target.type if target else "folder",
            "limit": safe_limit,
            "returned_count": len(entries),
            "complete": not truncated,
            "truncated": truncated,
            "truncation_reason": "entry_limit_exceeded" if truncated else "",
            "entries": entries,
            "head_commit_id": self._head(ctx),
        }

    async def find(
        self,
        ctx: ScopedFsContext,
        path: str = "",
        name: str = "",
        iname: str = "",
        path_glob: str = "",
        type: str = "any",
        conditions: list[dict[str, Any]] | str | None = None,
        mindepth: int = 0,
        max_depth: int = -1,
        limit: int = _DEFAULT_TREE_LIMIT,
        include_hidden: bool = True,
    ) -> dict[str, Any]:
        rel = self._clean_path(ctx, path)
        safe_limit = self._bounded_int(limit, _DEFAULT_TREE_LIMIT, _MAX_TREE_LIMIT)
        safe_min_depth = max(0, self._int_or_default(mindepth, 0))
        safe_max_depth = self._int_or_default(max_depth, -1)
        normalized_conditions = self._normalize_find_conditions(
            conditions,
            name=name,
            iname=iname,
            path_glob=path_glob,
            type_filter=type,
        )
        target = self._stat(ctx, rel, include_size=False)
        if target is None and rel:
            raise ScopedFsNotFound(f"Path not found: {rel}")
        entries: list[VersionEntry] = []
        if target:
            entries.append(target)
        raw_truncated = False
        if not target or target.type == "folder":
            if safe_max_depth != 0:
                tree_depth = safe_max_depth - 1 if safe_max_depth >= 0 else -1
                tree_entries = self.ops.list_tree_in_scope(
                    ctx.project_id,
                    ctx.scope_path,
                    rel,
                    max_depth=tree_depth,
                    include_size=False,
                    max_entries=safe_limit + 1,
                )
                raw_truncated = len(tree_entries) > safe_limit
                entries.extend(tree_entries[:safe_limit + 1])
        scanned_count = len(entries)
        entries = self._filter_entries(ctx, entries, include_hidden=include_hidden)
        entries = [
            entry
            for entry in entries
            if self._matches_find(
                ctx,
                entry,
                normalized_conditions,
                root_path=rel,
                mindepth=safe_min_depth,
                max_depth=safe_max_depth,
            )
        ]
        truncated = raw_truncated or len(entries) > safe_limit
        if truncated:
            entries = entries[:safe_limit]
        return {
            "path": rel,
            "scope": self._scope_payload(ctx),
            "limit": safe_limit,
            "returned_count": len(entries),
            "scanned_count": scanned_count,
            "complete": not truncated,
            "truncated": truncated,
            "truncation_reason": "entry_limit_exceeded" if truncated else "",
            "source": "live_tree",
            "entries": [self._entry_payload(ctx, entry) for entry in entries],
            "head_commit_id": self._head(ctx),
        }

    async def grep(
        self,
        ctx: ScopedFsContext,
        pattern: str,
        path: str = "",
        regex: bool = True,
        ignore_case: bool = False,
        invert_match: bool = False,
        only_matching: bool = False,
        include_hidden: bool = False,
        include: list[str] | str | None = None,
        exclude: list[str] | str | None = None,
        exclude_dir: list[str] | str | None = None,
        max_depth: int = -1,
        max_count: int = 0,
        require_file_list: bool = False,
        include_offsets: bool = False,
        word_match: bool = False,
        limit: int = _DEFAULT_GREP_LIMIT,
        max_files: int = _DEFAULT_GREP_MAX_FILES,
        max_bytes: int = _DEFAULT_GREP_MAX_BYTES,
        before_context: int = 0,
        after_context: int = 0,
    ) -> dict[str, Any]:
        if not pattern:
            raise ScopedFsError("INVALID_ARGUMENT", "pattern is required")
        rel = self._clean_path(ctx, path)
        safe_limit = self._bounded_int(limit, _DEFAULT_GREP_LIMIT, _MAX_GREP_LIMIT)
        safe_max_files = self._bounded_int(max_files, _DEFAULT_GREP_MAX_FILES, _MAX_GREP_MAX_FILES)
        safe_max_bytes = self._bounded_int(max_bytes, _DEFAULT_GREP_MAX_BYTES, _MAX_GREP_MAX_BYTES)
        safe_max_depth = self._int_or_default(max_depth, -1)
        safe_per_file_limit = max(0, self._int_or_default(max_count, 0))
        safe_before_context = max(0, min(self._int_or_default(before_context, 0), 100))
        safe_after_context = max(0, min(self._int_or_default(after_context, 0), 100))
        include_patterns = self._split_patterns(include)
        exclude_patterns = self._split_patterns(exclude)
        exclude_dir_patterns = self._split_patterns(exclude_dir)
        target = self._stat(ctx, rel, include_size=True)
        if target is None and rel:
            raise ScopedFsNotFound(f"Path not found: {rel}")
        indexed = None if require_file_list else self._try_indexed_grep(
            ctx,
            pattern=pattern,
            rel=rel,
            regex=regex,
            ignore_case=ignore_case,
            invert_match=invert_match,
            only_matching=only_matching,
            word_match=word_match,
            include_hidden=include_hidden,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            exclude_dir_patterns=exclude_dir_patterns,
            max_depth=safe_max_depth,
            per_file_limit=safe_per_file_limit,
            limit=safe_limit,
            max_files=safe_max_files,
            before_context=safe_before_context,
            after_context=safe_after_context,
            include_offsets=include_offsets,
        )
        if indexed is not None:
            return indexed
        candidates = [target] if target and target.type != "folder" else self.ops.list_tree_in_scope(
            ctx.project_id,
            ctx.scope_path,
            rel,
            max_depth=safe_max_depth,
            include_size=True,
            max_entries=safe_max_files + 1,
        )
        files = [
            entry
            for entry in candidates
            if entry and entry.type != "folder" and self._grep_candidate_allowed(
                ctx,
                self._entry_rel_path(ctx, entry),
                root_path=rel,
                include_hidden=include_hidden,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                exclude_dir_patterns=exclude_dir_patterns,
                max_depth=safe_max_depth,
            )
        ]
        truncated_files = len(files) > safe_max_files
        files = files[:safe_max_files]
        flags = re.IGNORECASE if ignore_case else 0
        py_pattern = pattern if regex else re.escape(pattern)
        if word_match:
            py_pattern = rf"\b(?:{py_pattern})\b"
        try:
            compiled = re.compile(py_pattern, flags)
        except re.error as exc:
            raise ScopedFsError("INVALID_ARGUMENT", f"Invalid regex: {exc}") from exc
        matches: list[dict[str, Any]] = []
        file_summaries: list[dict[str, Any]] = []
        scanned_files = 0
        scanned_bytes = 0
        skipped: list[dict[str, Any]] = []
        for entry in files:
            if len(matches) >= safe_limit:
                break
            entry_rel = self._entry_rel_path(ctx, entry)
            if entry.size_bytes and entry.size_bytes > safe_max_bytes:
                skipped.append({"path": entry_rel, "reason": "file_too_large"})
                continue
            try:
                content = self.ops.read_file_in_scope(ctx.project_id, ctx.scope_path, entry_rel)
            except Exception as exc:
                skipped.append({"path": entry_rel, "reason": str(exc)})
                continue
            scanned_files += 1
            scanned_bytes += len(content)
            if scanned_bytes > safe_max_bytes:
                skipped.append({"path": entry_rel, "reason": "byte_limit_exceeded"})
                break
            text = content.decode("utf-8", errors="replace")
            raw_lines = text.splitlines(keepends=True)
            line_items: list[tuple[str, int]] = []
            byte_cursor = 0
            for raw_line in raw_lines:
                clean_line = raw_line.rstrip("\r\n")
                line_items.append((clean_line, byte_cursor))
                byte_cursor += len(raw_line.encode("utf-8"))
            file_match_count = 0
            for index, (line, byte_offset) in enumerate(line_items):
                if len(matches) >= safe_limit:
                    break
                spans = [
                    (match.start(), match.end())
                    for match in compiled.finditer(line)
                    if match.start() != match.end()
                ]
                matched = bool(spans)
                if invert_match:
                    matched = not matched
                if not matched:
                    continue
                output_spans = spans if only_matching and not invert_match else [spans[0] if spans else (None, None)]
                for match_start, match_end in output_spans:
                    match_text = (
                        line[match_start:match_end]
                        if isinstance(match_start, int) and isinstance(match_end, int)
                        else ""
                    )
                    start_before = max(0, index - safe_before_context)
                    end_after = min(len(line_items), index + safe_after_context + 1)
                    match_byte_offset = (
                        byte_offset + len(line[:match_start].encode("utf-8"))
                        if isinstance(match_start, int)
                        else byte_offset
                    )
                    matches.append({
                        "path": entry_rel,
                        "line_number": index + 1,
                        "line_text": line,
                        "match_text": match_text,
                        "match_start": match_start,
                        "match_end": match_end,
                        "byte_offset": byte_offset if include_offsets else None,
                        "match_byte_offset": match_byte_offset if include_offsets else None,
                        "before_context": [
                            {"line_number": ctx_index + 1, "line_text": line_items[ctx_index][0]}
                            for ctx_index in range(start_before, index)
                        ],
                        "after_context": [
                            {"line_number": ctx_index + 1, "line_text": line_items[ctx_index][0]}
                            for ctx_index in range(index + 1, end_after)
                        ],
                        "content_hash": entry.content_hash,
                    })
                    file_match_count += 1
                    if len(matches) >= safe_limit:
                        break
                    if safe_per_file_limit and file_match_count >= safe_per_file_limit:
                        break
                if safe_per_file_limit and file_match_count >= safe_per_file_limit:
                    break
            if require_file_list or file_match_count:
                file_summaries.append({
                    "path": entry_rel,
                    "match_count": file_match_count,
                    "content_hash": entry.content_hash,
                })
        return {
            "path": rel,
            "pattern": pattern,
            "regex": regex,
            "ignore_case": ignore_case,
            "invert_match": invert_match,
            "only_matching": only_matching,
            "include_offsets": include_offsets,
            "require_file_list": require_file_list,
            "include": include_patterns,
            "exclude": exclude_patterns,
            "exclude_dir": exclude_dir_patterns,
            "max_depth": safe_max_depth,
            "max_count": safe_per_file_limit,
            "max_files": safe_max_files,
            "max_bytes": safe_max_bytes,
            "matches": matches,
            "files": file_summaries,
            "returned_count": len(matches),
            "matched_files": len([item for item in file_summaries if item.get("match_count", 0) > 0]),
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
            "complete": not truncated_files and len(matches) < safe_limit and not any(s.get("reason") == "byte_limit_exceeded" for s in skipped),
            "truncated": truncated_files or len(matches) >= safe_limit,
            "skipped": skipped,
            "head_commit_id": self._head(ctx),
        }

    def _try_indexed_grep(
        self,
        ctx: ScopedFsContext,
        *,
        pattern: str,
        rel: str,
        regex: bool,
        ignore_case: bool,
        invert_match: bool,
        only_matching: bool,
        word_match: bool,
        include_hidden: bool,
        include_patterns: list[str],
        exclude_patterns: list[str],
        exclude_dir_patterns: list[str],
        max_depth: int,
        per_file_limit: int,
        limit: int,
        max_files: int,
        before_context: int,
        after_context: int,
        include_offsets: bool,
    ) -> dict[str, Any] | None:
        try:
            envelope = run_indexed_grep_payload(
                project_id=ctx.project_id,
                scope_path=ctx.scope_path,
                excludes=list(ctx.exclude),
                ops=self.ops,
                pattern=pattern,
                path=rel,
                regex=regex,
                ignore_case=ignore_case,
                word_match=word_match,
                invert_match=invert_match,
                only_matching=only_matching,
                before_context=before_context,
                after_context=after_context,
                limit=limit,
                per_file_limit=per_file_limit,
                candidate_limit=max_files,
                pattern_max_chars=2048,
                max_limit=_MAX_GREP_LIMIT,
            )
        except IndexedGrepError:
            return None
        except Exception:
            return None

        if envelope.get("index_status") != "indexed":
            return None

        matches: list[dict[str, Any]] = []
        files_by_path: dict[str, dict[str, Any]] = {}
        for hit in envelope.get("hits") or []:
            hit_rel = relative_to_scope(str(hit.get("path") or ""), ctx.scope_path)
            if not self._grep_candidate_allowed(
                ctx,
                hit_rel,
                root_path=rel,
                include_hidden=include_hidden,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                exclude_dir_patterns=exclude_dir_patterns,
                max_depth=max_depth,
            ):
                continue
            col = max(0, int(hit.get("col") or 1) - 1)
            line_text = str(hit.get("match") or "")
            matches.append({
                "path": hit_rel,
                "line_number": hit.get("line") or 0,
                "line_text": line_text,
                "match_text": line_text,
                "match_start": col,
                "match_end": col + len(line_text),
                "byte_offset": None,
                "match_byte_offset": col if include_offsets else None,
                "before_context": [
                    {"line_text": text}
                    for text in (hit.get("context_before") or [])
                ],
                "after_context": [
                    {"line_text": text}
                    for text in (hit.get("context_after") or [])
                ],
                "content_hash": hit.get("content_hash") or None,
            })
            file_payload = files_by_path.setdefault(hit_rel, {
                "path": hit_rel,
                "match_count": 0,
                "content_hash": hit.get("content_hash") or None,
            })
            file_payload["match_count"] += 1

        truncated = bool(envelope.get("truncated"))
        return {
            "path": rel,
            "pattern": pattern,
            "regex": regex,
            "ignore_case": ignore_case,
            "invert_match": invert_match,
            "only_matching": only_matching,
            "include_offsets": include_offsets,
            "include": include_patterns,
            "exclude": exclude_patterns,
            "exclude_dir": exclude_dir_patterns,
            "max_depth": max_depth,
            "max_count": per_file_limit,
            "max_files": max_files,
            "matches": matches,
            "files": list(files_by_path.values()),
            "returned_count": len(matches),
            "matched_files": len(files_by_path),
            "scanned_files": 0,
            "scanned_bytes": 0,
            "complete": not truncated,
            "truncated": truncated,
            "skipped": [],
            "head_commit_id": self._head(ctx),
            "index_status": envelope.get("index_status"),
            "index_freshness": envelope.get("index_freshness"),
            "search_backend": "indexed",
        }

    async def cat(self, ctx: ScopedFsContext, path: str, structured: bool = False) -> dict[str, Any]:
        rel = self._clean_path(ctx, path, require=True)
        entry = self._stat(ctx, rel, include_size=True)
        if entry is None:
            raise ScopedFsNotFound(f"File not found: {rel}")
        if entry.type == "folder":
            # fs_cat advertises a file-content output schema; returning an ls
            # payload for a folder violates it for strict MCP clients. Point the
            # caller at fs_ls instead (POSIX `cat` on a directory is an error).
            raise ScopedFsError("IS_DIRECTORY", f"Path is a directory, use fs_ls: {rel}")
        content = self.ops.read_file_in_scope(ctx.project_id, ctx.scope_path, rel)
        text = content.decode("utf-8", errors="replace")
        content_json = None
        if structured and entry.type == "json":
            try:
                content_json = json.loads(text)
                text = ""
            except ValueError:
                pass
        return {
            "path": rel,
            "scope": self._scope_payload(ctx),
            "type": entry.type or detect_type(rel),
            "size_bytes": len(content),
            "content": content_json,
            "content_text": text,
            "head_commit_id": self._head(ctx),
        }

    async def head(
        self,
        ctx: ScopedFsContext,
        path: str,
        lines: int = 10,
        bytes: int | None = None,
    ) -> dict[str, Any]:
        rel = self._clean_path(ctx, path, require=True)
        content = self.ops.read_file_in_scope(ctx.project_id, ctx.scope_path, rel)
        if bytes is not None:
            output = content[: max(0, bytes)].decode("utf-8", errors="replace")
        else:
            output = "\n".join(content.decode("utf-8", errors="replace").splitlines()[: max(0, lines)])
            if output:
                output += "\n"
        return {"path": rel, "content_text": output, "bytes": len(output.encode("utf-8"))}

    async def tail(
        self,
        ctx: ScopedFsContext,
        path: str,
        lines: int = 10,
        bytes: int | None = None,
    ) -> dict[str, Any]:
        rel = self._clean_path(ctx, path, require=True)
        content = self.ops.read_file_in_scope(ctx.project_id, ctx.scope_path, rel)
        if bytes is not None:
            output = content[-max(0, bytes):].decode("utf-8", errors="replace") if bytes else ""
        else:
            output = "\n".join(content.decode("utf-8", errors="replace").splitlines()[-max(0, lines):])
            if output:
                output += "\n"
        return {"path": rel, "content_text": output, "bytes": len(output.encode("utf-8"))}

    async def stat(self, ctx: ScopedFsContext, path: str = "") -> dict[str, Any]:
        rel = self._clean_path(ctx, path)
        if not rel:
            return {
                "path": "",
                "scope": self._scope_payload(ctx),
                "exists": True,
                "type": "folder",
                "name": ctx.scope_path.rsplit("/", 1)[-1] if ctx.scope_path else "",
                "head_commit_id": self._head(ctx),
            }
        entry = self._stat(ctx, rel, include_size=True)
        if entry is None:
            return {
                "path": rel,
                "scope": self._scope_payload(ctx),
                "exists": False,
                "type": "",
                "head_commit_id": self._head(ctx),
            }
        data = self._entry_payload(ctx, entry)
        data.update({
            "exists": True,
            "scope": self._scope_payload(ctx),
            "head_commit_id": self._head(ctx),
        })
        return data

    async def write(
        self,
        ctx: ScopedFsContext,
        path: str,
        content: Any,
        node_type: str = "file",
        message: str = "",
        base_commit_id: str | None = None,
    ) -> dict[str, Any]:
        rel = self._clean_path(ctx, path, require=True)
        # serialize_content appends a node-type extension (.json/.md) AFTER the
        # exclusion gate above, so re-check the canonical path — otherwise an
        # exclude like "notes.json" is bypassable by writing "notes" as json.
        canonical = self._with_node_ext(rel, node_type or "file")
        if canonical != rel and self._is_excluded(ctx, canonical):
            raise ScopedFsPermissionDenied(
                f"Path is excluded from this MCP endpoint: {canonical}"
            )
        outcome = await self._run_write(
            self.commands.write_file(
                ctx.project_id,
                rel,
                content,
                node_type=node_type or "file",
                actor=ctx.actor,
                scope=ctx.scope_path,
                message=message,
                default_message_prefix="mcp write",
                base_commit_id=base_commit_id,
                defer_projection=True,
                source_channel="mcp",
            )
        )
        return {"path": outcome.path, "commit_id": outcome.result.commit_id, "scope": self._scope_payload(ctx)}

    async def mkdir(
        self,
        ctx: ScopedFsContext,
        path: str,
        parents: bool = False,
        base_commit_id: str | None = None,
    ) -> dict[str, Any]:
        rel = self._clean_path(ctx, path, require=True)
        if not parents:
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            if parent and self._stat(ctx, parent) is None:
                raise ScopedFsNotFound(f"No such parent directory: {parent}")
        outcome = await self._run_write(
            self.commands.mkdir(
                ctx.project_id,
                rel,
                actor=ctx.actor,
                scope=ctx.scope_path,
                message=f"mcp mkdir {rel}",
                base_commit_id=base_commit_id,
                defer_projection=True,
                source_channel="mcp",
            )
        )
        return {"path": rel, "commit_id": outcome.result.commit_id, "scope": self._scope_payload(ctx)}

    async def touch(
        self,
        ctx: ScopedFsContext,
        path: str = "",
        paths: list[str] | None = None,
        base_commit_id: str | None = None,
    ) -> dict[str, Any]:
        rels = self._clean_paths(ctx, paths or [path])
        missing = [rel for rel in rels if self._stat(ctx, rel) is None]
        existing = [rel for rel in rels if rel not in missing]
        commit_ids: list[str] = []
        if existing:
            outcome = await self._run_write(
                self.commands.touch(
                    ctx.project_id,
                    existing,
                    actor=ctx.actor,
                    scope=ctx.scope_path,
                    message=f"mcp touch {len(existing)} files",
                    base_commit_id=base_commit_id,
                    defer_projection=True,
                    source_channel="mcp",
                )
            )
            commit_ids.append(outcome.result.commit_id)
        for rel in missing:
            outcome = await self._run_write(
                self.commands.write_bytes(
                    ctx.project_id,
                    rel,
                    b"",
                    actor=ctx.actor,
                    scope=ctx.scope_path,
                    message=f"mcp touch {rel}",
                    base_commit_id=None if commit_ids else base_commit_id,
                    defer_projection=True,
                    source_channel="mcp",
                )
            )
            commit_ids.append(outcome.result.commit_id)
        return {"paths": rels, "commit_ids": commit_ids, "scope": self._scope_payload(ctx)}

    async def cp(
        self,
        ctx: ScopedFsContext,
        old_path: str,
        new_path: str,
        recursive: bool = False,
        no_clobber: bool = False,
        message: str = "",
        base_commit_id: str | None = None,
    ) -> dict[str, Any]:
        old_rel = self._clean_path(ctx, old_path, require=True)
        new_rel = self._clean_path(ctx, new_path, require=True)
        old_entry = self._stat(ctx, old_rel)
        if old_entry is None:
            raise ScopedFsNotFound(f"Path not found: {old_rel}")
        if old_entry.type == "folder" and not recursive:
            raise ScopedFsError("IS_DIRECTORY", f"Is a directory: {old_rel}")
        if no_clobber and self._stat(ctx, new_rel) is not None:
            return {"old_path": old_rel, "new_path": new_rel, "skipped": True, "reason": "destination exists"}
        outcome = await self._run_write(
            self.commands.copy(
                ctx.project_id,
                old_rel,
                new_rel,
                actor=ctx.actor,
                scope=ctx.scope_path,
                message=message or f"mcp copy {old_rel} -> {new_rel}",
                base_commit_id=base_commit_id,
                defer_projection=True,
                source_channel="mcp",
            )
        )
        return {"old_path": old_rel, "new_path": new_rel, "commit_id": outcome.result.commit_id, "skipped": False}

    async def mv(
        self,
        ctx: ScopedFsContext,
        old_path: str,
        new_path: str,
        no_clobber: bool = False,
        message: str = "",
        base_commit_id: str | None = None,
    ) -> dict[str, Any]:
        old_rel = self._clean_path(ctx, old_path, require=True)
        new_rel = self._clean_path(ctx, new_path, require=True)
        if self._stat(ctx, old_rel) is None:
            raise ScopedFsNotFound(f"Path not found: {old_rel}")
        if no_clobber and self._stat(ctx, new_rel) is not None:
            return {"old_path": old_rel, "new_path": new_rel, "skipped": True, "reason": "destination exists"}
        outcome = await self._run_write(
            self.commands.move(
                ctx.project_id,
                old_rel,
                new_rel,
                actor=ctx.actor,
                scope=ctx.scope_path,
                message=message or f"mcp move {old_rel} -> {new_rel}",
                base_commit_id=base_commit_id,
                defer_projection=True,
                source_channel="mcp",
            )
        )
        return {"old_path": old_rel, "new_path": new_rel, "commit_id": outcome.result.commit_id, "skipped": False}

    async def rmdir(
        self,
        ctx: ScopedFsContext,
        path: str = "",
        paths: list[str] | None = None,
        parents: bool = False,
        base_commit_id: str | None = None,
    ) -> dict[str, Any]:
        rels = self._clean_paths(ctx, paths or [path])
        remove_paths: list[str] = []
        for rel in rels:
            chain = self._rmdir_chain(ctx, rel, parents=parents)
            remove_paths.extend([p for p in chain if p not in remove_paths])
        outcome = await self._run_write(
            self.commands.delete(
                ctx.project_id,
                remove_paths,
                actor=ctx.actor,
                scope=ctx.scope_path,
                message=f"mcp rmdir {len(remove_paths)} directories",
                base_commit_id=base_commit_id,
                defer_projection=True,
                source_channel="mcp",
            )
        )
        return {"paths": rels, "removed_paths": remove_paths, "commit_id": outcome.result.commit_id}

    async def rm(
        self,
        ctx: ScopedFsContext,
        path: str = "",
        paths: list[str] | None = None,
        recursive: bool = False,
        force: bool = False,
        base_commit_id: str | None = None,
    ) -> dict[str, Any]:
        rels = self._clean_paths(ctx, paths or [path])
        existing: list[str] = []
        missing: list[str] = []
        for rel in rels:
            entry = self._stat(ctx, rel)
            if entry is None:
                missing.append(rel)
                continue
            if entry.type == "folder" and not recursive:
                raise ScopedFsError("IS_DIRECTORY", f"Is a directory: {rel}")
            existing.append(rel)
        if missing and not force:
            raise ScopedFsNotFound(f"Path not found: {missing[0]}")
        if not existing:
            return {"paths": rels, "removed": False, "commit_id": ""}
        outcome = await self._run_write(
            self.commands.delete(
                ctx.project_id,
                existing,
                actor=ctx.actor,
                scope=ctx.scope_path,
                message=f"mcp delete {len(existing)} paths",
                base_commit_id=base_commit_id,
                defer_projection=True,
                source_channel="mcp",
            )
        )
        return {"paths": existing, "removed": True, "commit_id": outcome.result.commit_id}

    async def _run_write(self, awaitable):
        try:
            return await awaitable
        except ConcurrentMutationError as exc:
            raise ScopedFsError("CONFLICT", str(exc), status_code=409) from exc
        except FileNotFoundError as exc:
            raise ScopedFsNotFound(str(exc)) from exc
        except ValueError as exc:
            raise ScopedFsError("INVALID_ARGUMENT", str(exc)) from exc

    def _clean_paths(self, ctx: ScopedFsContext, paths: list[str]) -> list[str]:
        rels = [self._clean_path(ctx, path, require=True) for path in paths]
        if not rels:
            raise ScopedFsError("INVALID_ARGUMENT", "path is required")
        return rels

    def _clean_path(self, ctx: ScopedFsContext, path: str = "", *, require: bool = False) -> str:
        try:
            rel = normalize_path(path or "")
        except ValueError as exc:
            raise ScopedFsError("INVALID_PATH", str(exc)) from exc
        if require and not rel:
            raise ScopedFsError("INVALID_ARGUMENT", "path is required")
        if self._is_excluded(ctx, rel):
            raise ScopedFsPermissionDenied(f"Path is excluded from this MCP endpoint: {rel}")
        return rel

    @staticmethod
    def _abs_path(ctx: ScopedFsContext, rel_path: str) -> str:
        """Lift a scope-relative path into the scope-absolute (project-relative)
        space that ``ctx.exclude`` and tree-reader ``entry.path`` values use."""
        rel = (rel_path or "").strip("/")
        if not ctx.scope_path:
            return rel
        return f"{ctx.scope_path}/{rel}" if rel else ctx.scope_path

    @staticmethod
    def _rel_path(ctx: ScopedFsContext, abs_path: str) -> str:
        """Inverse of :meth:`_abs_path`: drop the scope prefix so a scope-absolute
        entry path can be passed back into the scope-relative read APIs."""
        p = (abs_path or "").strip("/")
        if ctx.scope_path and (p == ctx.scope_path or p.startswith(ctx.scope_path + "/")):
            return p[len(ctx.scope_path):].strip("/")
        return p

    # Mirror of commands._EXT_MAP — node types whose serializer canonicalizes the
    # filename with an extension. Keep in sync with that map.
    _NODE_EXT = {"json": ".json", "markdown": ".md"}

    @classmethod
    def _with_node_ext(cls, rel: str, node_type: str) -> str:
        """The path serialize_content will actually write for this node_type."""
        ext = cls._NODE_EXT.get(node_type)
        if ext and not rel.endswith(ext):
            return f"{rel}{ext}"
        return rel

    def _is_excluded(self, ctx: ScopedFsContext, rel_path: str) -> bool:
        # ``rel_path`` is scope-relative. The admission layer stores ``exclude``
        # entries scope-absolute (project-relative) and merges them with the
        # carved child-scope paths, so the canonical comparison space is
        # absolute. Legacy rows may hold scope-relative entries, so match BOTH
        # forms — fail-closed is the right default for a deny gate. (Previously
        # only the relative form was compared, which let a known excluded path
        # be read/written directly even though listings hid it.)
        rel = (rel_path or "").strip("/")
        abs_path = self._abs_path(ctx, rel)
        for exclude in ctx.exclude:
            clean = normalize_path(str(exclude))
            if not clean:
                continue
            for candidate in (rel, abs_path):
                if candidate == clean or candidate.startswith(clean + "/"):
                    return True
        return False

    def _stat(self, ctx: ScopedFsContext, rel_path: str, *, include_size: bool = False) -> VersionEntry | None:
        if self._is_excluded(ctx, rel_path):
            return None
        return self.ops.stat_in_scope(ctx.project_id, ctx.scope_path, rel_path, include_size=include_size)

    def _filter_entries(
        self,
        ctx: ScopedFsContext,
        entries: list[VersionEntry],
        *,
        include_hidden: bool,
    ) -> list[VersionEntry]:
        # entry.path is scope-absolute; normalize to scope-relative so the
        # exclusion check sees the same space as the read/stat callers.
        visible = [
            entry for entry in entries
            if not self._is_excluded(ctx, self._rel_path(ctx, entry.path))
        ]
        if include_hidden:
            return visible
        return [entry for entry in visible if not self._is_hidden_path(self._entry_rel_path(ctx, entry))]

    @staticmethod
    def _entry_rel_path(ctx: ScopedFsContext, entry: VersionEntry) -> str:
        return relative_to_scope(entry.path or "", ctx.scope_path)

    def _entry_payload(self, ctx: ScopedFsContext, entry: VersionEntry) -> dict[str, Any]:
        rel_path = self._entry_rel_path(ctx, entry)
        return {
            "name": entry.name,
            "path": rel_path,
            "type": entry.type,
            "content_hash": getattr(entry, "content_hash", None),
            "size_bytes": getattr(entry, "size_bytes", None),
            "mime_type": getattr(entry, "mime_type", None),
            "children_count": getattr(entry, "children_count", None),
            "integrity_status": getattr(entry, "integrity_status", None),
            "created_at": getattr(entry, "created_at", None),
            "modified_at": getattr(entry, "modified_at", None),
        }

    def _grep_candidate_allowed(
        self,
        ctx: ScopedFsContext,
        rel_path: str,
        *,
        root_path: str,
        include_hidden: bool,
        include_patterns: list[str],
        exclude_patterns: list[str],
        exclude_dir_patterns: list[str],
        max_depth: int,
    ) -> bool:
        if self._is_excluded(ctx, rel_path):
            return False
        if not include_hidden and self._is_hidden_path(rel_path):
            return False
        if max_depth >= 0 and self._grep_file_depth(rel_path, root_path) > max_depth:
            return False
        if include_patterns and not any(self._matches_glob(rel_path, pattern) for pattern in include_patterns):
            return False
        if exclude_patterns and any(self._matches_glob(rel_path, pattern) for pattern in exclude_patterns):
            return False
        if exclude_dir_patterns and self._matches_exclude_dir(rel_path, exclude_dir_patterns):
            return False
        return True

    @staticmethod
    def _grep_file_depth(path: str, root_path: str = "") -> int:
        clean = path.strip("/")
        root = root_path.strip("/")
        if root and clean == root:
            return 0
        rel = clean[len(root) + 1:] if root and clean.startswith(f"{root}/") else clean
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if not parent:
            return 0
        return len([part for part in parent.split("/") if part])

    @staticmethod
    def _matches_glob(path: str, pattern: str) -> bool:
        clean = path.strip("/")
        base = clean.rsplit("/", 1)[-1]
        return fnmatch.fnmatchcase(clean, pattern) or fnmatch.fnmatchcase(base, pattern)

    @staticmethod
    def _matches_exclude_dir(path: str, patterns: list[str]) -> bool:
        parts = [part for part in path.strip("/").split("/")[:-1] if part]
        return any(fnmatch.fnmatchcase(part, pattern) for part in parts for pattern in patterns)

    @staticmethod
    def _split_patterns(value: list[str] | str | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.splitlines() if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]

    def _matches_find(
        self,
        ctx: ScopedFsContext,
        entry: VersionEntry,
        conditions: list[dict[str, Any]],
        *,
        root_path: str,
        mindepth: int,
        max_depth: int,
    ) -> bool:
        rel_path = self._entry_rel_path(ctx, entry)
        depth = self._find_depth(rel_path, root_path)
        if depth < mindepth:
            return False
        if max_depth >= 0 and depth > max_depth:
            return False
        for condition in conditions:
            matched = self._matches_find_condition(entry, rel_path, condition)
            if condition.get("negate"):
                matched = not matched
            if not matched:
                return False
        return True

    @staticmethod
    def _matches_find_condition(entry: VersionEntry, rel_path: str, condition: dict[str, Any]) -> bool:
        kind = str(condition.get("kind") or "").strip()
        value = str(condition.get("value") or "")
        if kind == "type":
            normalized = ScopedFsService._normalize_find_type(value)
            if normalized == "file":
                return entry.type != "folder"
            if normalized == "folder":
                return entry.type == "folder"
            return True
        if kind == "name":
            return fnmatch.fnmatchcase(entry.name, value)
        if kind == "iname":
            return fnmatch.fnmatch(entry.name.casefold(), value.casefold())
        if kind == "path":
            return fnmatch.fnmatchcase(rel_path or ".", value)
        return True

    @staticmethod
    def _normalize_find_type(value: str) -> str:
        if value in {"f", "file"}:
            return "file"
        if value in {"d", "folder", "directory"}:
            return "folder"
        return "any"

    @staticmethod
    def _find_depth(path: str, root_path: str = "") -> int:
        clean = path.strip("/")
        root = root_path.strip("/")
        if not clean or clean == root:
            return 0
        rel = clean[len(root) + 1:] if root and clean.startswith(f"{root}/") else clean
        return len([part for part in rel.split("/") if part])

    @staticmethod
    def _normalize_find_conditions(
        conditions: list[dict[str, Any]] | str | None,
        *,
        name: str,
        iname: str,
        path_glob: str,
        type_filter: str,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if isinstance(conditions, str) and conditions.strip():
            try:
                parsed = json.loads(conditions)
            except ValueError as exc:
                raise ScopedFsError("INVALID_ARGUMENT", f"Invalid find conditions JSON: {exc}") from exc
            conditions = parsed
        if isinstance(conditions, list):
            for item in conditions:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "").strip()
                if kind not in {"name", "iname", "path", "type"}:
                    continue
                normalized.append({
                    "kind": kind,
                    "value": str(item.get("value") or ""),
                    "negate": bool(item.get("negate")),
                })
        if name:
            normalized.append({"kind": "name", "value": name, "negate": False})
        if iname:
            normalized.append({"kind": "iname", "value": iname, "negate": False})
        if path_glob:
            normalized.append({"kind": "path", "value": path_glob, "negate": False})
        normalized_type = ScopedFsService._normalize_find_type(str(type_filter or "any"))
        if normalized_type != "any":
            normalized.append({"kind": "type", "value": normalized_type, "negate": False})
        return normalized

    @staticmethod
    def _bounded_int(value: Any, default: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        if parsed <= 0:
            return default
        return min(parsed, maximum)

    @staticmethod
    def _int_or_default(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _is_hidden_path(path: str) -> bool:
        return any(part.startswith(".") for part in path.strip("/").split("/") if part)

    @staticmethod
    def _scope_payload(ctx: ScopedFsContext) -> dict[str, Any]:
        return {
            "id": ctx.scope_id,
            "path": ctx.scope_path,
            "mode": ctx.mode,
            "exclude": ctx.exclude,
            "channel": ctx.channel,
        }

    def _head(self, ctx: ScopedFsContext) -> str:
        return self.ops.get_scope_head_commit_id(ctx.project_id, ctx.scope_path)

    def _rmdir_chain(self, ctx: ScopedFsContext, rel: str, *, parents: bool) -> list[str]:
        entry = self._stat(ctx, rel)
        if entry is None:
            raise ScopedFsNotFound(f"Path not found: {rel}")
        if entry.type != "folder":
            raise ScopedFsError("NOT_A_DIRECTORY", f"Not a directory: {rel}")
        if self.ops.list_dir_in_scope(ctx.project_id, ctx.scope_path, rel):
            raise ScopedFsError("DIRECTORY_NOT_EMPTY", f"Directory not empty: {rel}")
        removable = [rel]
        if not parents:
            return removable
        child = rel
        parent = child.rsplit("/", 1)[0] if "/" in child else ""
        while parent:
            entries = self.ops.list_dir_in_scope(ctx.project_id, ctx.scope_path, parent)
            remaining = [entry for entry in entries if entry.path.strip("/") != child]
            if remaining:
                break
            removable.append(parent)
            child = parent
            parent = child.rsplit("/", 1)[0] if "/" in child else ""
        return removable
