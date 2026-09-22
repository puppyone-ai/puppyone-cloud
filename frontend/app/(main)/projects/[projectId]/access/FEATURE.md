# Access Feature

## Create Access Picker

- The folder picker must follow the Data sidebar tree grammar: same row height, indent, folder icon, tree guide lines, and empty-folder copy.
- Folder rows are navigation controls. Clicking the row toggles expand/collapse only.
- Selecting a folder is a separate action on the far right. Available folders show a persistent checkbox so the next action is obvious.
- Existing access rows show text-only `Has access`, no checkbox. That text is still a deliberate select/open entry, while the row itself remains expand/collapse only.
- Root is always managed by Puppyone and shows `Has access` as non-clickable status.
- Do not put a permanent chevron before every folder. Expansion affordance should not add another fixed column that differs from the Data sidebar.

## Create Access Form

- `Access name` appears before `Path` and is marked required.
- `Path` is marked required and displayed as a readable breadcrumb, for example `Root / Company / Sales`.
- Connector descriptions should stay one line in the modal; shorten copy before allowing awkward wraps.
- Unsupported connector methods remain visible but disabled. MCP Server is supported and must create an MCP endpoint through `/api/v1/mcp-endpoints`. Sandbox is open: it is a scope-level Remote Dev (SSH) card on the Access page (`SandboxConnectCard`, backed by `/api/v1/scope-sandboxes/*`), reached via the card or the "Sandbox" / "SSH Terminal" menu entries — it is NOT created through the optional-connector checkbox (which stays unsupported because sandbox is a per-scope session, not a connector row).
- Modal typography uses three sizes only: 13px primary text/actions, 12px supporting text/status, and 11px uppercase labels/badges.

## AI Handoff CTA

- Copying an AI-agent setup prompt is a primary handoff action, not a neutral copy action.
- Use the shared `AiHandoffButton` capsule style for `Copy prompt` / `Copy setup prompt` buttons that move the user into Claude Code, Cursor, Codex, or another agent.
- Keep ordinary copy affordances neutral when they copy raw commands, access keys, URLs, or small inline values.
