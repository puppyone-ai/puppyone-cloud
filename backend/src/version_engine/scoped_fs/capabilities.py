"""Shared scoped filesystem capability metadata."""

from __future__ import annotations

from typing import Any

from .registry import build_mcp_tool_definitions


SEMANTICS: dict[str, Any] = {
    "summary": "PuppyOne FS is a Unix-like scoped cloud Context Drive backed by Version Engine commits.",
    "guarantees": [
        "Read and write operations are constrained to the active access-point or MCP endpoint scope.",
        "Excluded paths and carved child scopes are enforced server-side before filesystem operations run.",
        "Mutating commands apply to Version Engine history and audit logs; recovery is through history or rollback, not a local trash directory.",
        "Recursive reads and searches expose complete/truncated metadata so clients can detect bounded results.",
    ],
    "differences": [
        "POSIX inode, device, ownership, link, socket, symlink, chmod, chown, and chgrp semantics are not modeled.",
        "Timestamps are derived from Version Engine history when available, not local filesystem mtimes.",
        "Directory size and recursive counts may require extra metadata scans and should be requested explicitly.",
        "grep is a scoped cloud text search. The backend owns index readiness, fallback, scope, and resource-limit policy.",
        "upload and download are bridge operations between local files and the scoped cloud filesystem.",
    ],
    "resource_guidance": [
        "Default human output stays Unix-like; warnings and truncation diagnostics go to stderr.",
        "JSON output exposes complete, truncated, returned_count, limit, and truncation_reason where relevant.",
        "Prefer explicit paths over broad scans at the access-point root.",
        "Use tree -L, find -maxdepth, grep --max-depth, and --limit to bound recursive operations.",
        "Use head or tail for previews; raw file reads support range reads when available.",
    ],
    "discovery_guidance": [
        "Run `puppyone fs --help` to list scoped filesystem commands.",
        "Run `puppyone fs <command> --help` before using non-trivial flags.",
        "Use `puppyone fs semantics --json` for machine-readable capability notes.",
        "MCP clients should consume structured fs_* tool schemas rather than CLI flag strings.",
    ],
    "command_guidance": [
        "ls: Unix-like directory listing with common flags such as -l, -a, -R, -1, -h, -t, -d, and -F; JSON exposes completeness metadata.",
        "tree: directory tree rendering with depth, directory-only mode, hidden-entry support, and scan limits.",
        "find: scoped path discovery with name, path, type, depth, hidden-entry, and result-limit filters.",
        "cat/head/tail: raw file reads; head and tail are safer for previews and support line or byte bounds.",
        "stat: metadata lookup for a scoped path; root stat reports access-point scope state.",
        "grep: cloud text search using the canonical backend grep operation; clients only map their surface syntax to its request fields.",
        "write: creates or replaces one cloud file from explicit content, stdin, or local input depending on client surface.",
        "mkdir/touch: create directories or empty files; mkdir supports parent creation where the client exposes it.",
        "cp/mv: scoped copy, move, and rename with directory-target and no-clobber semantics where exposed.",
        "rm/rmdir: delete files, recursive trees, or empty directories according to the operation flags.",
        "upload/download: bridge local filesystem paths and scoped cloud paths; recursive forms should use depth and item limits.",
    ],
    "grep_guidance": [
        "Common CLI grep flags are mapped to backend request fields: pattern, regex, ignore_case, invert_match, only_matching, context, include/exclude globs, max_depth, max_files, max_bytes, and limit.",
        "The backend decides whether the text index is authoritative and falls back to a live tree scan when needed.",
        "Default pattern mode is regexp for the CLI; MCP clients should pass regex explicitly in structured arguments.",
        "Use --json or structured MCP responses when complete/truncated/scanned_files/scanned_bytes/skipped metadata matters.",
        "PCRE, null-data or null-delimited output, local device or symlink traversal, compressed-file modes, and stdin streaming are not modeled.",
    ],
    "find_guidance": [
        "CLI find expressions are mapped to backend request fields such as conditions, mindepth, max_depth, include_hidden, and limit.",
        "MCP clients should use fs_find structured predicates instead of CLI expression strings.",
        "The backend owns traversal, truncation, scope, exclude, hidden-entry, and future index/fallback policy.",
    ],
    "tools": [
        "fs_semantics",
        "fs_ls",
        "fs_tree",
        "fs_find",
        "fs_grep",
        "fs_cat",
        "fs_head",
        "fs_tail",
        "fs_stat",
        "fs_write",
        "fs_mkdir",
        "fs_touch",
        "fs_cp",
        "fs_mv",
        "fs_rmdir",
        "fs_rm",
    ],
}


def scoped_fs_capabilities(
    *,
    writable: bool,
    allowed_tools: set[str] | None = None,
) -> dict[str, Any]:
    """Return the shared capability document used by AP-FS and MCP."""

    return {
        "fs_semantics": SEMANTICS,
        "tools": build_mcp_tool_definitions(writable=writable, allowed_tools=allowed_tools),
    }
