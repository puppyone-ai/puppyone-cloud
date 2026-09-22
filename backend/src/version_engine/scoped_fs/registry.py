"""MCP filesystem tool registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ToolAccess = Literal["read", "write", "delete"]


@dataclass(frozen=True)
class FsToolSpec:
    name: str
    title: str
    description: str
    access: ToolAccess
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None


def _object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


_PATH = {"type": "string", "description": "Path relative to the MCP endpoint scope."}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 50000}
_BOOL = {"type": "boolean"}

_SCOPE_SCHEMA = _object_schema({
    "id": {"type": "string"},
    "path": {"type": "string"},
    "mode": {"type": "string", "enum": ["ro", "rw"]},
    "exclude": {"type": "array", "items": {"type": "string"}},
    "channel": {"type": "string"},
})

_ENTRY_SCHEMA = _object_schema({
    "name": {"type": "string"},
    "path": {"type": "string"},
    "type": {"type": "string"},
    "content_hash": {"type": ["string", "null"]},
    "size_bytes": {"type": ["integer", "null"]},
    "mime_type": {"type": ["string", "null"]},
    "children_count": {"type": ["integer", "null"]},
    "integrity_status": {"type": ["string", "null"]},
    "created_at": {"type": ["string", "null"]},
    "modified_at": {"type": ["string", "null"]},
})


def _output_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema = _object_schema(properties, required)
    schema["additionalProperties"] = True
    return schema


def _default_output_schema(tool_name: str) -> dict[str, Any]:
    base = {
        "path": {"type": "string"},
        "scope": _SCOPE_SCHEMA,
        "head_commit_id": {"type": "string"},
    }
    if tool_name == "fs_semantics":
        return _output_schema({"fs_semantics": {"type": "object"}, "scope": _SCOPE_SCHEMA}, ["fs_semantics", "scope"])
    if tool_name == "fs_ls":
        return _output_schema({
            **base,
            "target_type": {"type": "string"},
            "entries": {"type": "array", "items": _ENTRY_SCHEMA},
        }, ["path", "entries"])
    if tool_name == "fs_tree":
        return _output_schema({
            **base,
            "target_type": {"type": "string"},
            "limit": {"type": "integer"},
            "returned_count": {"type": "integer"},
            "complete": {"type": "boolean"},
            "truncated": {"type": "boolean"},
            "truncation_reason": {"type": "string"},
            "entries": {"type": "array", "items": _ENTRY_SCHEMA},
        }, ["path", "entries", "complete", "truncated"])
    if tool_name == "fs_find":
        return _output_schema({
            **base,
            "limit": {"type": "integer"},
            "returned_count": {"type": "integer"},
            "scanned_count": {"type": "integer"},
            "complete": {"type": "boolean"},
            "truncated": {"type": "boolean"},
            "truncation_reason": {"type": "string"},
            "source": {"type": "string"},
            "entries": {"type": "array", "items": _ENTRY_SCHEMA},
        }, ["path", "entries", "complete", "truncated"])
    if tool_name == "fs_grep":
        return _output_schema({
            "path": {"type": "string"},
            "pattern": {"type": "string"},
            "regex": {"type": "boolean"},
            "ignore_case": {"type": "boolean"},
            "invert_match": {"type": "boolean"},
            "only_matching": {"type": "boolean"},
            "include_offsets": {"type": "boolean"},
            "include": {"type": "array", "items": {"type": "string"}},
            "exclude": {"type": "array", "items": {"type": "string"}},
            "exclude_dir": {"type": "array", "items": {"type": "string"}},
            "max_depth": {"type": "integer"},
            "max_count": {"type": "integer"},
            "max_files": {"type": "integer"},
            "max_bytes": {"type": "integer"},
            "matches": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "files": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "returned_count": {"type": "integer"},
            "matched_files": {"type": "integer"},
            "scanned_files": {"type": "integer"},
            "scanned_bytes": {"type": "integer"},
            "complete": {"type": "boolean"},
            "truncated": {"type": "boolean"},
            "skipped": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "head_commit_id": {"type": "string"},
        }, ["path", "pattern", "matches", "complete", "truncated"])
    if tool_name == "fs_cat":
        return _output_schema({
            **base,
            "type": {"type": "string"},
            "size_bytes": {"type": "integer"},
            "content": {},
            "content_text": {"type": "string"},
        }, ["path"])
    if tool_name in {"fs_head", "fs_tail"}:
        return _output_schema({
            "path": {"type": "string"},
            "content_text": {"type": "string"},
            "bytes": {"type": "integer"},
        }, ["path", "content_text", "bytes"])
    if tool_name == "fs_stat":
        return _output_schema({
            **base,
            "exists": {"type": "boolean"},
            "type": {"type": "string"},
            "name": {"type": "string"},
        }, ["path", "exists", "type"])
    if tool_name in {"fs_write", "fs_mkdir"}:
        return _output_schema({
            "path": {"type": "string"},
            "commit_id": {"type": "string"},
            "scope": _SCOPE_SCHEMA,
        }, ["path", "commit_id"])
    if tool_name == "fs_touch":
        return _output_schema({
            "paths": {"type": "array", "items": {"type": "string"}},
            "commit_ids": {"type": "array", "items": {"type": "string"}},
            "scope": _SCOPE_SCHEMA,
        }, ["paths", "commit_ids"])
    if tool_name in {"fs_cp", "fs_mv"}:
        return _output_schema({
            "old_path": {"type": "string"},
            "new_path": {"type": "string"},
            "commit_id": {"type": "string"},
            "skipped": {"type": "boolean"},
            "reason": {"type": "string"},
        }, ["old_path", "new_path", "skipped"])
    if tool_name == "fs_rmdir":
        return _output_schema({
            "paths": {"type": "array", "items": {"type": "string"}},
            "removed_paths": {"type": "array", "items": {"type": "string"}},
            "commit_id": {"type": "string"},
        }, ["paths", "removed_paths", "commit_id"])
    if tool_name == "fs_rm":
        return _output_schema({
            "paths": {"type": "array", "items": {"type": "string"}},
            "removed": {"type": "boolean"},
            "commit_id": {"type": "string"},
        }, ["paths", "removed"])
    return _output_schema({})


def _default_annotations(spec: FsToolSpec) -> dict[str, Any]:
    read_only = spec.access == "read"
    destructive = spec.access == "delete" or spec.name in {"fs_write", "fs_mv", "fs_cp"}
    return {
        "title": spec.title,
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": read_only,
        "openWorldHint": False,
    }


FS_TOOL_SPECS: tuple[FsToolSpec, ...] = (
    FsToolSpec(
        "fs_semantics",
        "Filesystem Semantics",
        "Return machine-readable notes describing PuppyOne scoped filesystem behavior.",
        "read",
        _object_schema(),
    ),
    FsToolSpec(
        "fs_ls",
        "List Directory",
        "List entries at a path inside the endpoint scope.",
        "read",
        _object_schema({
            "path": _PATH,
            "include_hidden": _BOOL,
            "include_size": _BOOL,
        }),
    ),
    FsToolSpec(
        "fs_tree",
        "Directory Tree",
        "Recursively list entries inside the endpoint scope with truncation metadata.",
        "read",
        _object_schema({
            "path": _PATH,
            "max_depth": {"type": "integer", "minimum": -1, "maximum": 100},
            "limit": _LIMIT,
            "include_hidden": _BOOL,
            "include_size": _BOOL,
            "directories_only": _BOOL,
        }),
    ),
    FsToolSpec(
        "fs_find",
        "Find Paths",
        "Find files or folders by scoped path predicates.",
        "read",
        _object_schema({
            "path": _PATH,
            "name": {"type": "string", "description": "fnmatch glob against basename."},
            "iname": {"type": "string", "description": "case-insensitive fnmatch glob against basename."},
            "path_glob": {"type": "string", "description": "fnmatch glob against full scoped path."},
            "type": {"type": "string", "enum": ["any", "file", "folder", "f", "d"]},
            "conditions": {
                "type": "array",
                "description": "Ordered AND predicates; each may set negate=true. Kinds: name, iname, path, type.",
                "items": _object_schema({
                    "kind": {"type": "string", "enum": ["name", "iname", "path", "type"]},
                    "value": {"type": "string"},
                    "negate": _BOOL,
                }, ["kind", "value"]),
            },
            "mindepth": {"type": "integer", "minimum": 0, "maximum": 100},
            "max_depth": {"type": "integer", "minimum": -1, "maximum": 100},
            "limit": _LIMIT,
            "include_hidden": _BOOL,
        }),
    ),
    FsToolSpec(
        "fs_grep",
        "Search Text",
        "Search text files in the endpoint scope with regex or literal matching.",
        "read",
        _object_schema({
            "pattern": {"type": "string", "minLength": 1, "maxLength": 2048},
            "path": _PATH,
            "regex": _BOOL,
            "ignore_case": _BOOL,
            "invert_match": _BOOL,
            "only_matching": _BOOL,
            "include_hidden": _BOOL,
            "include": {"type": "array", "items": {"type": "string"}},
            "exclude": {"type": "array", "items": {"type": "string"}},
            "exclude_dir": {"type": "array", "items": {"type": "string"}},
            "max_depth": {"type": "integer", "minimum": -1, "maximum": 100},
            "max_count": {"type": "integer", "minimum": 0, "maximum": 20000},
            "require_file_list": _BOOL,
            "include_offsets": _BOOL,
            "word_match": _BOOL,
            "limit": _LIMIT,
            "max_files": _LIMIT,
            "max_bytes": {"type": "integer", "minimum": 1024, "maximum": 268435456},
            "before_context": {"type": "integer", "minimum": 0, "maximum": 100},
            "after_context": {"type": "integer", "minimum": 0, "maximum": 100},
        }, ["pattern"]),
    ),
    FsToolSpec(
        "fs_cat",
        "Read File",
        "Read a file as UTF-8 text, optionally parsing JSON.",
        "read",
        _object_schema({"path": _PATH, "structured": _BOOL}, ["path"]),
    ),
    FsToolSpec(
        "fs_head",
        "Read File Head",
        "Read the first lines or bytes from a file.",
        "read",
        _object_schema({
            "path": _PATH,
            "lines": {"type": "integer", "minimum": 0, "maximum": 10000},
            "bytes": {"type": "integer", "minimum": 0, "maximum": 10485760},
        }, ["path"]),
    ),
    FsToolSpec(
        "fs_tail",
        "Read File Tail",
        "Read the last lines or bytes from a file.",
        "read",
        _object_schema({
            "path": _PATH,
            "lines": {"type": "integer", "minimum": 0, "maximum": 10000},
            "bytes": {"type": "integer", "minimum": 0, "maximum": 10485760},
        }, ["path"]),
    ),
    FsToolSpec(
        "fs_stat",
        "Path Metadata",
        "Return metadata for a file, folder, or the endpoint scope root.",
        "read",
        _object_schema({"path": _PATH}),
    ),
    FsToolSpec(
        "fs_write",
        "Write File",
        "Create or replace a file inside a writable endpoint scope.",
        "write",
        _object_schema({
            "path": _PATH,
            "content": {"description": "String, object, array, number, boolean, or null content."},
            "node_type": {"type": "string", "enum": ["file", "markdown", "json"]},
            "message": {"type": "string"},
            "base_commit_id": {"type": "string"},
        }, ["path", "content"]),
    ),
    FsToolSpec(
        "fs_mkdir",
        "Create Directory",
        "Create a directory inside a writable endpoint scope.",
        "write",
        _object_schema({"path": _PATH, "parents": _BOOL, "base_commit_id": {"type": "string"}}, ["path"]),
    ),
    FsToolSpec(
        "fs_touch",
        "Touch Files",
        "Touch existing files or create empty files inside a writable endpoint scope.",
        "write",
        _object_schema({
            "path": _PATH,
            "paths": {"type": "array", "items": {"type": "string"}},
            "base_commit_id": {"type": "string"},
        }),
    ),
    FsToolSpec(
        "fs_cp",
        "Copy Path",
        "Copy a file or folder inside a writable endpoint scope.",
        "write",
        _object_schema({
            "old_path": _PATH,
            "new_path": _PATH,
            "recursive": _BOOL,
            "no_clobber": _BOOL,
            "message": {"type": "string"},
            "base_commit_id": {"type": "string"},
        }, ["old_path", "new_path"]),
    ),
    FsToolSpec(
        "fs_mv",
        "Move Path",
        "Move or rename a file or folder inside a writable endpoint scope.",
        "write",
        _object_schema({
            "old_path": _PATH,
            "new_path": _PATH,
            "no_clobber": _BOOL,
            "message": {"type": "string"},
            "base_commit_id": {"type": "string"},
        }, ["old_path", "new_path"]),
    ),
    FsToolSpec(
        "fs_rmdir",
        "Remove Directory",
        "Remove one or more empty directories inside a writable endpoint scope.",
        "delete",
        _object_schema({
            "path": _PATH,
            "paths": {"type": "array", "items": {"type": "string"}},
            "parents": _BOOL,
            "base_commit_id": {"type": "string"},
        }),
    ),
    FsToolSpec(
        "fs_rm",
        "Remove Path",
        "Remove files or folders inside a writable endpoint scope.",
        "delete",
        _object_schema({
            "path": _PATH,
            "paths": {"type": "array", "items": {"type": "string"}},
            "recursive": _BOOL,
            "force": _BOOL,
            "base_commit_id": {"type": "string"},
        }),
    ),
)

FS_TOOL_BY_NAME = {spec.name: spec for spec in FS_TOOL_SPECS}
MCP_FS_TOOL_NAMES = frozenset(FS_TOOL_BY_NAME)


def build_mcp_tool_definitions(
    *,
    writable: bool,
    allowed_tools: frozenset[str] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return MCP protocol tool definitions for a scope mode."""

    effective_allowed = allowed_tools
    if effective_allowed is None:
        effective_allowed = {
            spec.name
            for spec in FS_TOOL_SPECS
            if spec.access == "read" or (writable and spec.access == "write")
        }

    tools: list[dict[str, Any]] = []
    for spec in FS_TOOL_SPECS:
        if spec.access in {"write", "delete"} and not writable:
            continue
        if spec.name not in effective_allowed:
            continue
        tools.append({
            "name": spec.name,
            "title": spec.title,
            "description": spec.description,
            "inputSchema": spec.input_schema,
            "outputSchema": spec.output_schema or _default_output_schema(spec.name),
            "annotations": spec.annotations or _default_annotations(spec),
        })
    return tools
