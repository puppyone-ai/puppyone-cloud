"""MCP filesystem tool exposure policy.

The MCP client config only authenticates the client. Tool exposure is a
server-side policy: list only allowed tools, and reject direct calls for
disabled tools.
"""

from __future__ import annotations

from typing import Any

from src.platform.access.filesystem.registry import FS_TOOL_SPECS, MCP_FS_TOOL_NAMES


READ_TOOL_NAMES = frozenset(spec.name for spec in FS_TOOL_SPECS if spec.access == "read")
WRITE_TOOL_NAMES = frozenset(spec.name for spec in FS_TOOL_SPECS if spec.access == "write")
DELETE_TOOL_NAMES = frozenset(spec.name for spec in FS_TOOL_SPECS if spec.access == "delete")


def default_mcp_fs_allowed_tools(*, writable: bool) -> frozenset[str]:
    """Default policy for new/legacy endpoints.

    Read tools are on. Non-destructive write tools follow the endpoint scope's
    writable upper bound. Delete tools default off and must be enabled
    explicitly.
    """

    allowed = set(READ_TOOL_NAMES)
    if writable:
        allowed.update(WRITE_TOOL_NAMES)
    return frozenset(allowed)


def resolve_mcp_fs_allowed_tools(
    tools_config: Any,
    *,
    writable: bool,
) -> frozenset[str]:
    """Resolve a raw endpoint ``tools_config`` value to concrete tool names.

    Supported shapes:
      - missing / legacy external-tool list: server defaults
      - {"filesystem": {"allowed": ["fs_ls", ...]}}
      - {"fs": {"allowed_tools": ["fs_ls", ...]}}
      - {"filesystem": {"groups": {"read": true, "write": true, "delete": false}}}
      - {"filesystem": {"tools": {"fs_ls": true, "fs_rm": false}}}

    The endpoint scope remains the upper bound: write/delete tools are removed
    when the resolved scope is read-only.
    """

    default_allowed = default_mcp_fs_allowed_tools(writable=writable)
    if tools_config is None:
        return default_allowed

    fs_config = _filesystem_config(tools_config)
    if fs_config is None:
        allowed_from_legacy = _allowed_from_legacy_list(tools_config)
        return _apply_scope_upper_bound(
            allowed_from_legacy if allowed_from_legacy is not None else default_allowed,
            writable=writable,
        )

    raw_allowed = (
        _string_list(fs_config.get("allowed"))
        or _string_list(fs_config.get("allowed_tools"))
        or _string_list(fs_config.get("tools_allowed"))
    )
    if raw_allowed is not None:
        return _apply_scope_upper_bound(raw_allowed, writable=writable)

    raw_tool_map = fs_config.get("tools")
    if isinstance(raw_tool_map, dict):
        explicit = {
            name
            for name, enabled in raw_tool_map.items()
            if enabled is True and isinstance(name, str) and name in MCP_FS_TOOL_NAMES
        }
        return _apply_scope_upper_bound(explicit, writable=writable)

    raw_groups = fs_config.get("groups")
    if isinstance(raw_groups, dict):
        return _apply_scope_upper_bound(_allowed_from_groups(raw_groups), writable=writable)

    # Backward-compatible group booleans directly under filesystem/fs.
    if any(key in fs_config for key in ("read", "write", "delete")):
        return _apply_scope_upper_bound(_allowed_from_groups(fs_config), writable=writable)

    return default_allowed


def custom_tool_bindings_from_tools_config(tools_config: Any) -> list[dict[str, Any]]:
    """Return legacy/custom ToolRepository bindings stored in tools_config."""

    if isinstance(tools_config, list):
        return [item for item in tools_config if isinstance(item, dict)]
    if isinstance(tools_config, dict):
        for key in ("custom_tools", "bound_tools", "external_tools"):
            value = tools_config.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _filesystem_config(tools_config: Any) -> dict[str, Any] | None:
    if not isinstance(tools_config, dict):
        return None
    for key in ("filesystem", "fs"):
        value = tools_config.get(key)
        if isinstance(value, dict):
            return value
    # Accept a direct policy object for callers that store just the FS policy.
    if any(key in tools_config for key in ("allowed", "allowed_tools", "tools", "groups", "read", "write", "delete")):
        return tools_config
    return None


def _string_list(value: Any) -> set[str] | None:
    if not isinstance(value, list):
        return None
    return {item for item in value if isinstance(item, str) and item in MCP_FS_TOOL_NAMES}


def _allowed_from_legacy_list(value: Any) -> set[str] | None:
    """Support a list of {"name": "fs_ls", "enabled": true} items if present."""

    if not isinstance(value, list):
        return None
    seen_policy_items = False
    allowed: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name") or item.get("tool_name")
        if not isinstance(raw_name, str) or raw_name not in MCP_FS_TOOL_NAMES:
            continue
        seen_policy_items = True
        if item.get("enabled", True) is True:
            allowed.add(raw_name)
    return allowed if seen_policy_items else None


def _allowed_from_groups(groups: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    if groups.get("read", True) is not False:
        allowed.update(READ_TOOL_NAMES)
    if groups.get("write", True) is not False:
        allowed.update(WRITE_TOOL_NAMES)
    if groups.get("delete", False) is True:
        allowed.update(DELETE_TOOL_NAMES)
    return allowed


def _apply_scope_upper_bound(
    allowed: set[str] | frozenset[str],
    *,
    writable: bool,
) -> frozenset[str]:
    sanitized = {name for name in allowed if name in MCP_FS_TOOL_NAMES}
    if not writable:
        sanitized.difference_update(WRITE_TOOL_NAMES)
        sanitized.difference_update(DELETE_TOOL_NAMES)
    return frozenset(sanitized)
