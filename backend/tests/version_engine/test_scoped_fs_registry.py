from __future__ import annotations

from src.version_engine.scoped_fs.registry import build_mcp_tool_definitions


def _tools(writable: bool) -> dict[str, dict]:
    return {tool["name"]: tool for tool in build_mcp_tool_definitions(writable=writable)}


def _tool_names(writable: bool) -> set[str]:
    return set(_tools(writable))


def test_readonly_endpoint_exposes_read_tools_only():
    names = _tool_names(writable=False)

    assert {
        "fs_semantics",
        "fs_ls",
        "fs_tree",
        "fs_find",
        "fs_grep",
        "fs_cat",
        "fs_head",
        "fs_tail",
        "fs_stat",
    }.issubset(names)
    assert "fs_write" not in names
    assert "fs_rm" not in names
    assert "ls" not in names


def test_writable_endpoint_exposes_each_fs_command_as_a_tool():
    names = _tool_names(writable=True)

    assert {
        "fs_write",
        "fs_mkdir",
        "fs_touch",
        "fs_cp",
        "fs_mv",
    }.issubset(names)
    assert "fs_rmdir" not in names
    assert "fs_rm" not in names
    assert "fs_batch" not in names


def test_allowed_tools_can_explicitly_expose_delete_tools():
    tools = build_mcp_tool_definitions(
        writable=True,
        allowed_tools=frozenset({"fs_ls", "fs_rm"}),
    )
    names = {tool["name"] for tool in tools}

    assert names == {"fs_ls", "fs_rm"}


def test_tool_definitions_include_full_mcp_contract_fields():
    tool = _tools(writable=True)["fs_ls"]

    assert tool["title"] == "List Directory"
    assert tool["description"]
    assert tool["inputSchema"]["type"] == "object"
    assert tool["outputSchema"]["type"] == "object"
    assert "entries" in tool["outputSchema"]["properties"]
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["annotations"]["destructiveHint"] is False
    assert tool["annotations"]["openWorldHint"] is False


def test_find_and_grep_schemas_match_canonical_fs_fields():
    tools = _tools(writable=False)
    find_props = tools["fs_find"]["inputSchema"]["properties"]
    grep_props = tools["fs_grep"]["inputSchema"]["properties"]

    assert {"conditions", "mindepth", "max_depth", "include_hidden"}.issubset(find_props)
    assert find_props["conditions"]["items"]["properties"]["kind"]["enum"] == ["name", "iname", "path", "type"]
    assert {
        "invert_match",
        "only_matching",
        "include_hidden",
        "include",
        "exclude",
        "exclude_dir",
        "max_depth",
        "max_count",
        "require_file_list",
        "include_offsets",
        "word_match",
    }.issubset(grep_props)


def test_write_and_delete_tools_carry_mutation_annotations():
    tools = {
        tool["name"]: tool
        for tool in build_mcp_tool_definitions(
            writable=True,
            allowed_tools=frozenset({"fs_write", "fs_rm"}),
        )
    }

    assert tools["fs_write"]["annotations"]["readOnlyHint"] is False
    assert tools["fs_write"]["annotations"]["destructiveHint"] is True
    assert tools["fs_rm"]["annotations"]["readOnlyHint"] is False
    assert tools["fs_rm"]["annotations"]["destructiveHint"] is True
