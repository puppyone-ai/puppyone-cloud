from __future__ import annotations

from src.platform.access.filesystem.policy import resolve_mcp_fs_allowed_tools


def test_default_policy_keeps_delete_tools_off():
    allowed = resolve_mcp_fs_allowed_tools(None, writable=True)

    assert "fs_ls" in allowed
    assert "fs_write" in allowed
    assert "fs_rm" not in allowed
    assert "fs_rmdir" not in allowed


def test_policy_allowed_list_is_scope_bounded():
    allowed = resolve_mcp_fs_allowed_tools(
        {
            "version": 1,
            "filesystem": {
                "allowed": ["fs_ls", "fs_write", "fs_rm"],
            },
        },
        writable=False,
    )

    assert allowed == frozenset({"fs_ls"})


def test_policy_groups_can_enable_delete_explicitly():
    allowed = resolve_mcp_fs_allowed_tools(
        {
            "filesystem": {
                "groups": {"read": True, "write": True, "delete": True},
            },
        },
        writable=True,
    )

    assert "fs_ls" in allowed
    assert "fs_write" in allowed
    assert "fs_rm" in allowed
